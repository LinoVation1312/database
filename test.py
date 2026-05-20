import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from matplotlib.backends.backend_pdf import PdfPages
import io
import re

# ─────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Acoustic DB",
    page_icon="🔊",
    layout="wide",
)

st.markdown("""
<style>
    [data-testid="stSidebar"] { background: #0f1117; }
    [data-testid="stSidebar"] * { color: #e8e8e8 !important; }
    .block-container { padding-top: 1.5rem; }
    h1 { font-size: 1.6rem !important; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
# HELPERS – material parsing
# ─────────────────────────────────────────────

MATERIAL_MAP = [
    (r'\bGlass\s*[Ff]iber\b|\bGF\b',            'GF'),
    (r'\bPES\b',                                 'PES'),
    (r'\bPP\b',                                  'PP'),
    (r'\bPET\b',                                 'PET'),
    (r'\bPANOX\b|\bPAnOx\b|\bPANox\b',          'PANox'),
]

def parse_materials(description: str) -> str:
    """
    Return material abbreviations in the order they appear in the description.
    Scans the description left-to-right and builds an ordered, deduplicated list.
    """
    if not description or not isinstance(description, str):
        return "?"
    # Find all matches with their positions
    hits = []
    for pattern, label in MATERIAL_MAP:
        for m in re.finditer(pattern, description, re.IGNORECASE):
            hits.append((m.start(), label))
    hits.sort(key=lambda x: x[0])
    # Deduplicate while preserving order
    seen, found = set(), []
    for _, label in hits:
        if label not in seen:
            seen.add(label)
            found.append(label)
    return "+".join(found) if found else "?"


def parse_airgap(text: str) -> str | None:
    """
    Detect airgap mention anywhere in a string.
    Handles patterns like:
      '10mm airgap', '10 mm airgap', 'airgap 20mm', 'airgap 20 mm',
      '+airgap 20mm', '10mm air gap', etc.
    Returns '10 mm' / '20 mm' etc., or None.
    """
    if not text or not isinstance(text, str):
        return None
    pattern = r'(\d+)\s*mm\s*air\s*gap|air\s*gap\s*(\d+)\s*mm'
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        val = m.group(1) or m.group(2)
        return f"{val} mm"
    return None


def build_curve_label(row: pd.Series) -> str:
    """
    Build human-readable curve label:
    <STN> | <Materials> | <SurfaceMass> gsm | <Thickness> mm [| AG <X> mm]
    """
    stn   = str(row.get("sample_number_stn", "?")).strip()
    mat   = parse_materials(str(row.get("detailed_description", "")))
    mass  = row.get("surface_mass_gm2")
    thick = row.get("thickness_mm")

    mass_str  = f"{int(mass)} gsm"  if pd.notna(mass)  and str(mass).replace('.','').isdigit() else "? gsm"
    thick_str = f"{thick} mm"       if pd.notna(thick)                                          else "? mm"

    # Airgap: check both orientation and description fields
    airgap = (
        parse_airgap(str(row.get("material_orientation", "")))
        or parse_airgap(str(row.get("detailed_description", "")))
    )
    ag_str = f" | AG {airgap}" if airgap else ""

    return f"{stn} | {mat} | {mass_str} | {thick_str}{ag_str}"


# ─────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────

@st.cache_data(show_spinner="Chargement des données…")
def load_data(file_bytes: bytes) -> pd.DataFrame | None:
    buf = io.BytesIO(file_bytes)

    # ── GNRL sheet ──────────────────────────────
    xf = pd.ExcelFile(buf, engine="openpyxl")
    gnrl_sheet = next(
        (s for s in xf.sheet_names if s.strip().upper().startswith("GNRL")), None
    )
    data_sheet = next(
        (s for s in xf.sheet_names if s.strip().upper() == "DATA"), None
    )
    if gnrl_sheet is None or data_sheet is None:
        st.error("Feuilles 'GNRL…' ou 'DATA' introuvables.")
        return None

    gnrl = pd.read_excel(buf, sheet_name=gnrl_sheet, engine="openpyxl", header=6)

    # Normalise columns
    def norm(cols):
        return (
            cols.str.strip()
                .str.lower()
                .str.replace(r'[^\w\s]', '', regex=True)
                .str.replace(r'\s+', '_', regex=True)
        )
    gnrl.columns = norm(gnrl.columns)

    # Keep only rows that look like real samples (col B = sample_number_stn)
    stn_col = next((c for c in gnrl.columns if 'sample_number_stn' in c), None)
    if stn_col is None:
        st.error("Colonne 'Sample Number (STN)' introuvable dans GNRL.")
        return None

    # The duplicate STN column: keep only the short one (E0001 format)
    stn_cols = [c for c in gnrl.columns if 'sample_number_stn' in c]
    # Pick the one whose values look like E\d+
    for c in stn_cols:
        sample_vals = gnrl[c].dropna().astype(str)
        if sample_vals.str.match(r'^E\d+$').any():
            stn_col = c
            break

    gnrl = gnrl.rename(columns={stn_col: "sample_number_stn"})
    # Drop other STN duplicates
    for c in gnrl.columns:
        if 'sample_number_stn' in c and c != "sample_number_stn":
            gnrl = gnrl.drop(columns=[c])

    gnrl = gnrl[gnrl["sample_number_stn"].astype(str).str.match(r'^E\d+$')]
    gnrl["sample_number_stn"] = gnrl["sample_number_stn"].astype(str).str.strip()

    # Standardise key column names (flexible matching)
    rename_map = {
        "trim_level":             ["trim_level"],
        "material_family":        ["material_family"],
        "material_orientation":   ["material_orientation"],
        "material_supplier":      ["material_supplier"],
        "detailed_description":   ["detailed_description"],
        "surface_mass_gm2":       ["surface_mass_gm", "surface_mass_gm2"],
        "thickness_mm":           ["thickness_mm"],
        "assembly_type":          ["assembly_type"],
    }
    for target, candidates in rename_map.items():
        if target not in gnrl.columns:
            for cand in candidates:
                match = next((c for c in gnrl.columns if cand in c), None)
                if match:
                    gnrl = gnrl.rename(columns={match: target})
                    break

    gnrl["surface_mass_gm2"] = pd.to_numeric(gnrl.get("surface_mass_gm2"), errors="coerce")
    gnrl["thickness_mm"]     = pd.to_numeric(gnrl.get("thickness_mm"),     errors="coerce")

    # ── DATA sheet ──────────────────────────────
    data = pd.read_excel(buf, sheet_name=data_sheet, engine="openpyxl")
    data.columns = norm(data.columns)
    data = data.rename(columns={
        next((c for c in data.columns if 'sample_number' in c), data.columns[0]): "sample_number_stn",
        next((c for c in data.columns if 'alpha_cabin' in c),  "alpha_cabin"):    "alpha_cabin",
        next((c for c in data.columns if 'alpha_kundt' in c),  "alpha_kundt"):    "alpha_kundt",
    })

    # Forward-fill STN (merged cells / blank rows)
    data["sample_number_stn"] = data["sample_number_stn"].astype(str).replace("nan", pd.NA).ffill()
    data["sample_number_stn"] = data["sample_number_stn"].str.strip()
    data = data[data["sample_number_stn"].str.match(r'^E\d+$', na=False)]

    data["frequency"]   = pd.to_numeric(data.get("frequency"),   errors="coerce")
    data["alpha_cabin"] = pd.to_numeric(data.get("alpha_cabin"), errors="coerce")
    data["alpha_kundt"] = pd.to_numeric(data.get("alpha_kundt"), errors="coerce")

    # ── Merge ────────────────────────────────────
    merged = data.merge(gnrl, on="sample_number_stn", how="left")

    # Build curve label
    merged["curve_label"] = merged.apply(build_curve_label, axis=1)

    return merged


# ─────────────────────────────────────────────
# MAIN UI
# ─────────────────────────────────────────────
st.title("🔊 Visualisation des Courbes d'Absorption Acoustique")

uploaded_file = st.file_uploader(
    "Chargez votre fichier Excel (Database_Vx.xlsx)", type=["xlsx"]
)

if not uploaded_file:
    st.info("⬆️ Chargez un fichier Excel pour commencer.")
    st.stop()

df = load_data(uploaded_file.read())
if df is None:
    st.stop()

# ─── Sidebar filters ───────────────────────────
st.sidebar.header("Filtres")

# Trim level
trim_opts = sorted(df["trim_level"].dropna().unique()) if "trim_level" in df.columns else []
trim_sel = st.sidebar.multiselect("Trim Level", trim_opts, default=trim_opts)

# Supplier
sup_opts = sorted(df["material_supplier"].dropna().unique()) if "material_supplier" in df.columns else []
sup_sel = st.sidebar.multiselect("Supplier", sup_opts, default=sup_opts)

# Surface mass
mass_min = int(df["surface_mass_gm2"].min(skipna=True))
mass_max = int(df["surface_mass_gm2"].max(skipna=True))
mass_range = st.sidebar.slider("Surface Mass (g/m²)", mass_min, mass_max, (mass_min, mass_max))

# Thickness
thick_min = float(df["thickness_mm"].min(skipna=True))
thick_max = float(df["thickness_mm"].max(skipna=True))
thick_range = st.sidebar.slider("Épaisseur (mm)", thick_min, thick_max, (thick_min, thick_max))

# Assembly type
asm_opts = sorted(df["assembly_type"].dropna().unique()) if "assembly_type" in df.columns else []
asm_sel = st.sidebar.multiselect("Assembly Type", asm_opts, default=asm_opts)

# Apply filters
fdf = df.copy()
if trim_sel:
    fdf = fdf[fdf["trim_level"].isin(trim_sel)]
if sup_sel:
    fdf = fdf[fdf["material_supplier"].isin(sup_sel)]
fdf = fdf[fdf["surface_mass_gm2"].between(*mass_range)]
fdf = fdf[fdf["thickness_mm"].between(*thick_range)]
if asm_sel:
    fdf = fdf[fdf["assembly_type"].isin(asm_sel)]

# Sample selection
st.sidebar.markdown("---")
available_labels = sorted(fdf["curve_label"].dropna().unique())
selected_labels = st.sidebar.multiselect(
    f"Échantillons ({len(available_labels)} disponibles)",
    available_labels,
)

# Absorption type
abs_type = st.sidebar.radio("Type d'absorption", ["alpha_cabin", "alpha_kundt"])

FREQ_TICKS = {
    "alpha_cabin": [315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000],
    "alpha_kundt": [200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300],
}

# ─── Plot ──────────────────────────────────────
if not selected_labels:
    st.warning("Sélectionnez au moins un échantillon dans la barre latérale.")
    st.stop()

plot_data = fdf[fdf["curve_label"].isin(selected_labels)]
ticks = FREQ_TICKS[abs_type]

# Matplotlib style
plt.rcParams.update({
    "font.family":      "DejaVu Sans",
    "axes.spines.top":  False,
    "axes.spines.right":False,
    "axes.grid":        True,
    "grid.linestyle":   "--",
    "grid.alpha":       0.4,
    "figure.dpi":       130,
})

COLORS = [
    "#2563EB","#DC2626","#16A34A","#D97706","#7C3AED",
    "#0891B2","#DB2777","#65A30D","#EA580C","#4338CA",
    "#0D9488","#9333EA","#B45309","#059669","#E11D48",
]

fig, ax = plt.subplots(figsize=(13, 6))

for i, label in enumerate(selected_labels):
    sub = plot_data[plot_data["curve_label"] == label].sort_values("frequency")
    sub = sub[sub[abs_type].notna() & sub["frequency"].notna()]
    if sub.empty:
        continue
    color = COLORS[i % len(COLORS)]
    ax.plot(
        sub["frequency"], sub[abs_type],
        marker="o", markersize=4, linewidth=1.8,
        color=color, label=label
    )

ax.set_xscale("log")
ax.set_xticks(ticks)
ax.get_xaxis().set_major_formatter(ticker.ScalarFormatter())
ax.set_xticklabels([str(f) for f in ticks], rotation=30, ha="right", fontsize=8)
ax.set_xlim(ticks[0] * 0.9, ticks[-1] * 1.05)
ax.set_ylim(bottom=0)
ax.set_xlabel("Fréquence (Hz)", fontsize=10)
ax.set_ylabel("Coefficient d'absorption α", fontsize=10)
ax.set_title(f"Absorption acoustique — {abs_type}", fontsize=12, fontweight="bold", pad=12)

# Legend outside plot on the right
ax.legend(
    title="Échantillons",
    title_fontsize=8,
    fontsize=7.5,
    loc="upper left",
    bbox_to_anchor=(1.01, 1),
    borderaxespad=0,
    framealpha=0.9,
)
fig.tight_layout(rect=[0, 0, 0.72, 1])  # leave room for legend

st.pyplot(fig, use_container_width=True)

# ─── Downloads ────────────────────────────────
col1, col2 = st.columns(2)

pdf_buf = io.BytesIO()
with PdfPages(pdf_buf) as pdf:
    pdf.savefig(fig, bbox_inches="tight")
pdf_buf.seek(0)
col1.download_button("📄 Télécharger PDF", pdf_buf, "courbes_absorption.pdf", "application/pdf")

jpg_buf = io.BytesIO()
fig.savefig(jpg_buf, format="jpeg", dpi=150, bbox_inches="tight")
jpg_buf.seek(0)
col2.download_button("🖼️ Télécharger JPEG", jpg_buf, "courbes_absorption.jpeg", "image/jpeg")

# ─── Data table (optional) ────────────────────
with st.expander("📊 Voir les données brutes"):
    cols_show = ["sample_number_stn", "curve_label", "frequency", abs_type]
    st.dataframe(
        plot_data[cols_show].sort_values(["sample_number_stn", "frequency"]),
        use_container_width=True,
        hide_index=True,
    )

# ─── Footer ───────────────────────────────────
st.markdown(
    '<p style="color:#888;font-size:12px;text-align:center;margin-top:3rem;">'
    'GitHub: <a href="https://github.com/LinoVation1312/database" '
    'style="color:#888;" target="_blank">https://github.com/LinoVation1312/database</a>'
    ' · Lino CONORD, 2024</p>',
    unsafe_allow_html=True,
)
