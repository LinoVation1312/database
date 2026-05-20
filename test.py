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
# HELPERS
# ─────────────────────────────────────────────

def norm_cols(cols):
    """Normalize column names: strip, lowercase, remove special chars, spaces→underscore."""
    return (
        cols.str.strip()
            .str.lower()
            .str.replace(r'[^\w\s]', '', regex=True)
            .str.replace(r'\s+', '_', regex=True)
    )

# Material abbreviation patterns — ordered so longer/more specific patterns come first
MATERIAL_MAP = [
    (r'\bGlass\s*[Ff]iber\b', 'GF'),
    (r'\bPANox\b|\bPANOX\b',  'PANox'),
    (r'\bPES\b',               'PES'),
    (r'\bPET\b',               'PET'),
    (r'\bPP\b',                'PP'),
]

def parse_materials(description: str) -> str:
    """Return material abbreviations in order of appearance."""
    if not description or not isinstance(description, str):
        return "?"
    hits = []
    for pattern, label in MATERIAL_MAP:
        for m in re.finditer(pattern, description, re.IGNORECASE):
            hits.append((m.start(), label))
    hits.sort(key=lambda x: x[0])
    seen, found = set(), []
    for _, label in hits:
        if label not in seen:
            seen.add(label)
            found.append(label)
    return "+".join(found) if found else "?"


def parse_airgap(text: str):
    """
    Detect airgap value from any of:
      '10mm airgap', '10 mm airgap', 'airgap 20mm', '+airgap 20mm',
      '10mm air gap', 'air gap 20 mm', etc.
    Returns '10 mm' or None.
    """
    if not text or not isinstance(text, str):
        return None
    m = re.search(r'(\d+)\s*mm\s*air\s*?gap|air\s*?gap\s*(\d+)\s*mm', text, re.IGNORECASE)
    if m:
        val = m.group(1) or m.group(2)
        return f"{val} mm"
    return None


def build_curve_label(row: pd.Series, mass_col: str) -> str:
    """
    Build human-readable label:
    <STN> | <Materials> | <Mass> gsm | <Thickness> mm [| AG <X> mm]
    """
    stn   = str(row.get("sample_number_stn", "?")).strip()
    mat   = parse_materials(str(row.get("detailed_description", "")))
    mass  = row.get(mass_col)
    thick = row.get("thickness_mm")

    try:
        mass_str = f"{int(float(mass))} gsm"
    except (TypeError, ValueError):
        mass_str = "? gsm"

    thick_str = f"{thick} mm" if pd.notna(thick) else "? mm"

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
def load_data(file_bytes: bytes):
    buf = io.BytesIO(file_bytes)
    xf  = pd.ExcelFile(buf, engine="openpyxl")

    # ── Locate sheets ─────────────────────────
    gnrl_sheet = next((s for s in xf.sheet_names if s.strip().upper().startswith("GNRL")), None)
    data_sheet = next((s for s in xf.sheet_names if s.strip().upper() == "DATA"), None)
    if not gnrl_sheet or not data_sheet:
        st.error(f"Feuilles introuvables. Sheets disponibles : {xf.sheet_names}")
        return None, None

    # ── GNRL sheet ────────────────────────────
    # Auto-detect header row: first row where col B contains "Sample Number"
    raw = pd.read_excel(buf, sheet_name=gnrl_sheet, engine="openpyxl", header=None)
    header_row = None
    for i, row in raw.iterrows():
        vals = [str(v).strip().lower() for v in row if pd.notna(v)]
        if any("sample number" in v for v in vals):
            header_row = i
            break
    if header_row is None:
        st.error("Ligne d'en-tête introuvable dans la feuille GNRL.")
        return None, None

    gnrl = pd.read_excel(buf, sheet_name=gnrl_sheet, engine="openpyxl", header=header_row)
    gnrl.columns = norm_cols(gnrl.columns)

    # Drop fully empty rows
    gnrl = gnrl.dropna(how="all")

    # Identify the two STN columns: full name (23-1400P-…) vs short (E0001)
    stn_cols = [c for c in gnrl.columns if "sample_number_stn" in c]
    if len(stn_cols) < 1:
        st.error(f"Colonne STN introuvable. Colonnes disponibles : {gnrl.columns.tolist()}")
        return None, None

    # Pick the short STN column (values match E\d+)
    short_col = None
    for c in stn_cols:
        sample = gnrl[c].dropna().astype(str).str.strip()
        if sample.str.match(r'^E\d+$').mean() > 0.5:
            short_col = c
            break
    if short_col is None:
        short_col = stn_cols[-1]  # fallback

    # Rename to canonical name and drop the other STN column
    gnrl = gnrl.rename(columns={short_col: "stn"})
    for c in stn_cols:
        if c != short_col and c in gnrl.columns:
            gnrl = gnrl.drop(columns=[c])

    gnrl["stn"] = gnrl["stn"].astype(str).str.strip()
    gnrl = gnrl[gnrl["stn"].str.match(r'^E\d+$')]

    # Find surface mass column (handles ² or 2 suffix variants)
    mass_col = next(
        (c for c in gnrl.columns if re.search(r'surface_mass', c)),
        None
    )
    if mass_col is None:
        st.error(f"Colonne 'surface_mass' introuvable. Colonnes : {gnrl.columns.tolist()}")
        return None, None

    gnrl[mass_col]       = pd.to_numeric(gnrl[mass_col],      errors="coerce")
    gnrl["thickness_mm"] = pd.to_numeric(gnrl.get("thickness_mm"), errors="coerce")

    # ── DATA sheet ────────────────────────────
    data = pd.read_excel(buf, sheet_name=data_sheet, engine="openpyxl")
    data.columns = norm_cols(data.columns)

    # Canonical column names
    col_map = {}
    for c in data.columns:
        if "sample_number" in c or (data[c].dropna().astype(str).str.match(r'^E\d+$').mean() > 0.3):
            col_map[c] = "stn"
            break
    for c in data.columns:
        if "alpha_cabin" in c:
            col_map[c] = "alpha_cabin"
        elif "alpha_kundt" in c:
            col_map[c] = "alpha_kundt"
        elif "frequency" in c:
            col_map[c] = "frequency"
    data = data.rename(columns=col_map)

    # Forward-fill STN (merged cells / blank continuation rows)
    if "stn" in data.columns:
        data["stn"] = (
            data["stn"].astype(str)
                       .replace({"nan": pd.NA, "None": pd.NA})
                       .ffill()
                       .str.strip()
        )
        data = data[data["stn"].str.match(r'^E\d+$', na=False)]
    else:
        st.error("Colonne STN introuvable dans DATA.")
        return None, None

    for c in ["frequency", "alpha_cabin", "alpha_kundt"]:
        if c in data.columns:
            data[c] = pd.to_numeric(data[c], errors="coerce")

    # ── Merge ─────────────────────────────────
    merged = data.merge(gnrl, on="stn", how="left")
    merged["curve_label"] = merged.apply(lambda r: build_curve_label(r, mass_col), axis=1)

    return merged, mass_col


# ─────────────────────────────────────────────
# MAIN UI
# ─────────────────────────────────────────────
st.title("🔊 Visualisation des Courbes d'Absorption Acoustique")

uploaded_file = st.file_uploader("Chargez votre fichier Excel (Database_Vx.xlsx)", type=["xlsx"])

if not uploaded_file:
    st.info("⬆️ Chargez un fichier Excel pour commencer.")
    st.stop()

df, mass_col = load_data(uploaded_file.read())
if df is None:
    st.stop()

# ─── Sidebar filters ───────────────────────────
st.sidebar.header("Filtres")

def multiselect_all(label, series):
    opts = sorted(series.dropna().unique())
    return st.sidebar.multiselect(label, opts, default=opts)

trim_sel    = multiselect_all("Trim Level",    df["trim_level"])    if "trim_level"    in df.columns else []
sup_sel     = multiselect_all("Supplier",      df["material_supplier"]) if "material_supplier" in df.columns else []
asm_sel     = multiselect_all("Assembly Type", df["assembly_type"]) if "assembly_type" in df.columns else []

mass_min  = int(df[mass_col].min(skipna=True))
mass_max  = int(df[mass_col].max(skipna=True))
mass_range = st.sidebar.slider("Surface Mass (g/m²)", mass_min, mass_max, (mass_min, mass_max))

thick_min  = float(df["thickness_mm"].min(skipna=True))
thick_max  = float(df["thickness_mm"].max(skipna=True))
thick_range = st.sidebar.slider("Épaisseur (mm)", thick_min, thick_max, (thick_min, thick_max))

# Apply filters
fdf = df.copy()
if trim_sel    and "trim_level"       in fdf.columns: fdf = fdf[fdf["trim_level"].isin(trim_sel)]
if sup_sel     and "material_supplier" in fdf.columns: fdf = fdf[fdf["material_supplier"].isin(sup_sel)]
if asm_sel     and "assembly_type"    in fdf.columns: fdf = fdf[fdf["assembly_type"].isin(asm_sel)]
fdf = fdf[fdf[mass_col].between(*mass_range)]
fdf = fdf[fdf["thickness_mm"].between(*thick_range)]

# Sample selection
st.sidebar.markdown("---")
available_labels = sorted(fdf["curve_label"].dropna().unique())
selected_labels  = st.sidebar.multiselect(
    f"Échantillons ({len(available_labels)} disponibles)", available_labels
)

abs_type = st.sidebar.radio("Type d'absorption", ["alpha_cabin", "alpha_kundt"])

FREQ_TICKS = {
    "alpha_cabin": [315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000],
    "alpha_kundt": [200, 250, 315, 400, 500, 630,  800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300],
}

# ─── Plot ──────────────────────────────────────
if not selected_labels:
    st.warning("Sélectionnez au moins un échantillon dans la barre latérale.")
    st.stop()

if abs_type not in df.columns:
    st.error(f"Colonne '{abs_type}' absente des données.")
    st.stop()

plot_data = fdf[fdf["curve_label"].isin(selected_labels)]
ticks     = FREQ_TICKS[abs_type]

plt.rcParams.update({
    "font.family":       "DejaVu Sans",
    "axes.spines.top":   False,
    "axes.spines.right": False,
    "axes.grid":         True,
    "grid.linestyle":    "--",
    "grid.alpha":        0.4,
    "figure.dpi":        130,
})

COLORS = [
    "#2563EB","#DC2626","#16A34A","#D97706","#7C3AED",
    "#0891B2","#DB2777","#65A30D","#EA580C","#4338CA",
    "#0D9488","#9333EA","#B45309","#059669","#E11D48",
]

fig, ax = plt.subplots(figsize=(13, 6))

for i, label in enumerate(selected_labels):
    sub = (
        plot_data[plot_data["curve_label"] == label]
        .sort_values("frequency")
        .dropna(subset=["frequency", abs_type])
    )
    if sub.empty:
        continue
    ax.plot(
        sub["frequency"], sub[abs_type],
        marker="o", markersize=4, linewidth=1.8,
        color=COLORS[i % len(COLORS)], label=label,
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
ax.legend(
    title="Échantillons", title_fontsize=8, fontsize=7.5,
    loc="upper left", bbox_to_anchor=(1.01, 1),
    borderaxespad=0, framealpha=0.9,
)
fig.tight_layout(rect=[0, 0, 0.72, 1])

st.pyplot(fig, use_container_width=True)

# ─── Downloads ─────────────────────────────────
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

# ─── Raw data expander ─────────────────────────
with st.expander("📊 Voir les données brutes"):
    show_cols = [c for c in ["stn", "curve_label", "frequency", abs_type] if c in plot_data.columns]
    st.dataframe(
        plot_data[show_cols].sort_values(["stn", "frequency"]),
        use_container_width=True, hide_index=True,
    )

# ─── Footer ────────────────────────────────────
st.markdown(
    '<p style="color:#888;font-size:12px;text-align:center;margin-top:3rem;">'
    'GitHub: <a href="https://github.com/LinoVation1312/database" style="color:#888;" '
    'target="_blank">https://github.com/LinoVation1312/database</a>'
    ' · Lino CONORD, 2024</p>',
    unsafe_allow_html=True,
)
