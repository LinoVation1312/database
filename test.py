import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import io
import re
import requests
import base64

# ─────────────────────────────────────────────────────────────────
# CONFIGURATION GITHUB
# ─────────────────────────────────────────────────────────────────
# Remplissez vos informations ici ou utilisez les secrets Streamlit
GITHUB_USER = "VOTRE_NOM_UTILISATEUR_GITHUB"
GITHUB_REPO = "VOTRE_NOM_DE_DEPOT"
FILE_PATH = "Database_Vx.xlsx"  # Chemin du fichier dans le dépôt
BRANCH = "main"

# Récupération du token sécurisé depuis les Secrets Streamlit Cloud
# En local, vous pouvez le mettre dans .streamlit/secrets.toml : GITHUB_TOKEN = "your_token"
if "GITHUB_TOKEN" in st.secrets:
    TOKEN = st.secrets["GITHUB_TOKEN"]
else:
    TOKEN = None

# URLs GitHub API et Raw
RAW_URL = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{BRANCH}/{FILE_PATH}"
API_URL = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{FILE_PATH}"

# ─────────────────────────────────────────────────────────────────
# PAGE CONFIGURATION
# ─────────────────────────────────────────────────────────────────
st.set_page_config(page_title="DATABASE", page_icon="🔊", layout="wide")

st.markdown("""
<style>
    [data-testid="stSidebar"] { background-color: #0f1117; }
    [data-testid="stSidebar"] * { color: #e8e8e8 !important; }
    .stMetric { background-color: #1e212b; padding: 15px; border-radius: 10px; border: 1px solid #333; width: fit-content; }
    h1 { color: #3b82f6 !important; font-weight: 800 !important; }
    .composite-info-box {
        background-color: #1a1f2e;
        border-left: 4px solid #7c3aed;
        border-radius: 6px;
        padding: 10px 14px;
        margin-bottom: 10px;
        font-size: 0.85em;
        color: #c4b5fd;
    }
    .ref-info-box {
        background-color: #1c1a10;
        border-left: 4px solid #f59e0b;
        border-radius: 6px;
        padding: 10px 14px;
        margin-bottom: 10px;
        font-size: 0.85em;
        color: #fcd34d;
    }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────
# FONCTIONS GITHUB (LECTURE / ÉCRITURE)
# ─────────────────────────────────────────────────────────────────
@st.cache_data(ttl=60) # Cache de 60 secondes pour éviter de bloquer l'application
def download_excel_from_github():
    """Télécharge le fichier Excel depuis le GitHub public ou privé (si token fourni)"""
    headers = {}
    if TOKEN:
        headers["Authorization"] = f"token {TOKEN}"
    
    response = requests.get(RAW_URL, headers=headers)
    if response.status_code == 200:
        return response.content
    else:
        return None

def upload_excel_to_github(file_bytes):
    """Met à jour le fichier Excel sur GitHub en faisant un commit automatique via l'API"""
    if not TOKEN:
        st.error("❌ Le jeton GITHUB_TOKEN est manquant dans les secrets de l'application.")
        return False
        
    headers = {
        "Authorization": f"token {TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    # 1. On doit récupérer le 'sha' du fichier existant pour pouvoir le remplacer
    res = requests.get(API_URL, headers=headers)
    sha = None
    if res.status_code == 200:
        sha = res.json().get("sha")
        
    # 2. Encodage du nouveau fichier en base64
    content_b64 = base64.b64encode(file_bytes).decode("utf-8")
    
    # 3. Préparation de la requête de mise à jour
    data = {
        "message": "Mise à jour de la base de données via Streamlit App",
        "content": content_b64,
        "branch": BRANCH
    }
    if sha:
        data["sha"] = sha
        
    # 4. Envoi de la modification
    put_res = requests.put(API_URL, headers=headers, json=data)
    if put_res.status_code in [200, 201]:
        return True
    else:
        st.error(f"Erreur API GitHub : {put_res.text}")
        return False

# ─────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS (Inchangé)
# ─────────────────────────────────────────────────────────────────
def norm_cols(cols):
    return (cols.str.strip().str.lower()
            .str.replace(r'[^\w\s]', '', regex=True)
            .str.replace(r'\s+', '_', regex=True))

MATERIAL_MAP = [
    (r'\bGlass\s*[Ff]iber\b', 'GF'), (r'\bPANox\b|\bPANOX\b', 'PANox'),
    (r'\bPES\b', 'PES'), (r'\bPET\b', 'PET'), (r'\bPP\b', 'PP')
]

STN_PATTERN = r'^(E\d+|REF\s+\S.*)$'

def parse_materials(description: str) -> str:
    if not isinstance(description, str): return "?"
    hits = [(m.start(), label) for pattern, label in MATERIAL_MAP
            for m in re.finditer(pattern, description, re.IGNORECASE)]
    hits.sort(key=lambda x: x[0])
    seen, found = set(), []
    for _, label in hits:
        if label not in seen:
            seen.add(label); found.append(label)
    return "+".join(found) if found else "?"

def parse_airgap(text: str):
    if not isinstance(text, str): return None
    m = re.search(r'(\d+)\s*mm\s*air\s*?gap|air\s*?gap\s*(\d+)\s*mm', text, re.IGNORECASE)
    return f"{m.group(1) or m.group(2)} mm" if m else None

def is_ref(row: pd.Series) -> bool:
    return str(row.get("stn", "")).upper().startswith("REF ")

def is_composite(row: pd.Series) -> bool:
    if is_ref(row): return False
    stn  = str(row.get("stn", "")).upper()
    desc = str(row.get("detailed_description", ""))
    if "COMP" in stn: return True
    if "+" in desc:
        parts = desc.split("+")
        if len(parts) >= 2:
            mat_in = lambda t: any(re.search(pat, t, re.IGNORECASE) for pat, _ in MATERIAL_MAP)
            if mat_in(parts[0]) and mat_in("+".join(parts[1:])): return True
    return False

def parse_composite_layers(description: str, mass_col_val):
    if not isinstance(description, str): return None, None
    parts = description.split("+")
    if len(parts) < 2: return None, None

    def layer_info(text, fallback_mass=None):
        mats = parse_materials(text)
        mass_m = re.search(r'(\d[\d,]*)\s*(?:gsm|gm|g/m²|g/m2)', text, re.IGNORECASE)
        mass = (mass_m.group(1).replace(",", "") if mass_m
                else (str(int(float(fallback_mass))) if pd.notna(fallback_mass) else "?"))
        thick_m = re.search(r'(\d+(?:[.,]\d+)?)\s*mm', text, re.IGNORECASE)
        thick = thick_m.group(1) if thick_m else None
        label = f"{mats}  {mass} gsm"
        if thick: label += f"  {thick} mm"
        return label

    l1 = layer_info(parts[0].strip(), fallback_mass=mass_col_val)
    l2 = layer_info("+".join(parts[1:]).strip())
    return l1, l2

def build_curve_label(row: pd.Series, mass_col: str) -> str:
    stn   = str(row.get("stn", "?")).strip()
    desc  = str(row.get("detailed_description", ""))
    mass  = row.get(mass_col)
    thick = row.get("thickness_mm")

    if is_ref(row): return f"★ {stn}"

    thick_str = f"{thick} mm" if pd.notna(thick) else "? mm"
    airgap    = parse_airgap(str(row.get("material_orientation", ""))) or parse_airgap(desc)
    ag_str    = f" | AG {airgap}" if airgap else ""

    if is_composite(row):
        l1, l2 = parse_composite_layers(desc, mass)
        if l1 and l2: return f"⊕ {stn} | [{l1}] + [{l2}] | {thick_str}{ag_str}"
        mat      = parse_materials(desc)
        mass_str = f"{int(float(mass))} gsm" if pd.notna(mass) else "? gsm"
        return f"⊕ {stn} | {mat} | {mass_str} | {thick_str}{ag_str}"

    mat      = parse_materials(desc)
    mass_str = f"{int(float(mass))} gsm" if pd.notna(mass) else "? gsm"
    return f"{stn} | {mat} | {mass_str} | {thick_str}{ag_str}"

# ─────────────────────────────────────────────────────────────────
# DATA LOADING & CLEANING (Inchangé sauf entrée bytes)
# ─────────────────────────────────────────────────────────────────
def load_data(file_bytes: bytes):
    buf = io.BytesIO(file_bytes)
    xf  = pd.ExcelFile(buf, engine="openpyxl")

    gnrl_sheet = next((s for s in xf.sheet_names if s.strip().upper().startswith("GNRL")), None)
    data_sheet = next((s for s in xf.sheet_names if s.strip().upper() == "DATA"), None)

    if not gnrl_sheet or not data_sheet:
        st.error("❌ Sheets 'GNRL' or 'DATA' not found in the workbook.")
        return None, None

    raw = pd.read_excel(buf, sheet_name=gnrl_sheet, engine="openpyxl", header=None)
    header_row = next(
        (i for i, r in raw.iterrows()
         if any("sample" in str(v).lower() or "stn" in str(v).lower() for v in r if pd.notna(v))),
        None
    )
    if header_row is None:
        st.error("❌ Header row could not be identified in the GNRL sheet.")
        return None, None

    gnrl = pd.read_excel(buf, sheet_name=gnrl_sheet, engine="openpyxl", header=header_row)
    gnrl.columns = norm_cols(gnrl.columns)
    gnrl = gnrl.dropna(how="all")

    stn_cols = [c for c in gnrl.columns if "stn" in c or "sample" in c]
    if not stn_cols:
        st.error(f"❌ STN column not found in GNRL sheet.")
        return None, None

    short_col = next(
        (c for c in stn_cols if gnrl[c].dropna().astype(str).str.strip().str.match(r'^E\d+').mean() > 0.4),
        stn_cols[-1]
    )
    gnrl = gnrl.rename(columns={short_col: "stn"})
    gnrl["stn"] = gnrl["stn"].astype(str).str.strip().str.upper()
    gnrl = gnrl[gnrl["stn"].str.match(STN_PATTERN, na=False)]

    mass_col = next((c for c in gnrl.columns if "surface_mass" in c), None)
    if mass_col:
        gnrl[mass_col] = pd.to_numeric(gnrl[mass_col], errors="coerce")
    gnrl["thickness_mm"] = pd.to_numeric(gnrl.get("thickness_mm"), errors="coerce")

    gnrl["is_ref"]       = gnrl.apply(is_ref, axis=1)
    gnrl["is_composite"] = gnrl.apply(is_composite, axis=1)

    data = pd.read_excel(buf, sheet_name=data_sheet, engine="openpyxl")
    data.columns = norm_cols(data.columns)

    if "stn" not in data.columns:
        stn_data_col = next((c for c in data.columns if "stn" in c or "sample" in c), None)
        if stn_data_col: data = data.rename(columns={stn_data_col: "stn"})
        else: return None, None

    for c in data.columns:
        if "alpha_cabin" in c: data = data.rename(columns={c: "alpha_cabin"})
        elif "alpha_kundt" in c: data = data.rename(columns={c: "alpha_kundt"})
        elif "frequency"   in c: data = data.rename(columns={c: "frequency"})

    data["stn"] = (data["stn"].astype(str)
                   .replace({"nan": pd.NA, "None": pd.NA, "": pd.NA})
                   .ffill().str.strip().str.upper())
    data = data[data["stn"].str.match(STN_PATTERN, na=False)]

    for col in ["frequency", "alpha_cabin", "alpha_kundt"]:
        if col in data.columns: data[col] = pd.to_numeric(data[col], errors="coerce")

    merged = data.merge(gnrl, on="stn", how="left")
    merged["is_ref"]       = merged["is_ref"].fillna(False)
    merged["is_composite"] = merged["is_composite"].fillna(False)
    merged["curve_label"]  = merged.apply(lambda r: build_curve_label(r, mass_col), axis=1)
    return merged, mass_col

# ─────────────────────────────────────────────────────────────────
# MAIN UI
# ─────────────────────────────────────────────────────────────────
st.title("🔊 DATABASE")

# --- ZONE ADMINISTRATION (Gestion de la base de données) ---
with st.sidebar.expander("🔄 Base de données GitHub", expanded=False):
    uploaded_file = st.file_uploader("Écraser la base actuelle (Excel)", type=["xlsx"])
    if uploaded_file:
        file_bytes = uploaded_file.read()
        if st.button("🚀 Pousser sur GitHub"):
            with st.spinner("Téléversement sur GitHub en cours..."):
                success = upload_excel_to_github(file_bytes)
                if success:
                    st.success("✅ Fichier mis à jour ! Actualisation...")
                    st.cache_data.clear() # On vide le cache pour forcer la relecture
                    st.rerun()

# --- CHARGEMENT AUTOMATIQUE ---
excel_data = download_excel_from_github()

if excel_data is None:
    st.error("❌ Impossible de charger le fichier depuis GitHub. Vérifiez la configuration ou uploadez un fichier manuellement ci-dessous.")
    uploaded_file_fallback = st.file_uploader("Fichier de secours", type=["xlsx"])
    if uploaded_file_fallback:
        excel_data = uploaded_file_fallback.read()
    else:
        st.stop()

df, mass_col = load_data(excel_data)
if df is None:
    st.stop()

# ─────────────────────────────────────────────────────────────────
# SIDEBAR FILTERS
# ─────────────────────────────────────────────────────────────────
st.sidebar.header("🎛️ Global Filters")

n_comp   = int(df[~df["is_ref"]].groupby("stn")["is_composite"].first().sum())
n_single = int((~df[~df["is_ref"]].groupby("stn")["is_composite"].first()).sum())
sample_type = st.sidebar.radio(
    "Sample Type",
    ["All", "Single Layer Only", "Composite Only"],
    index=0,
    help=f"{n_comp} composite · {n_single} single-layer"
)
st.sidebar.markdown("---")

trim_sel = (st.sidebar.multiselect("Trim Level", sorted(df["trim_level"].dropna().unique())) if "trim_level" in df.columns else [])
sup_sel  = (st.sidebar.multiselect("Material Supplier", sorted(df["material_supplier"].dropna().unique())) if "material_supplier" in df.columns else [])

non_ref = df[~df["is_ref"]]
m_min, m_max = float(non_ref[mass_col].min(skipna=True) or 0), float(non_ref[mass_col].max(skipna=True) or 100)
mass_range   = st.sidebar.slider("Surface Mass (g/m²)", m_min, m_max, (m_min, m_max))

t_min, t_max = float(non_ref["thickness_mm"].min(skipna=True) or 0), float(non_ref["thickness_mm"].max(skipna=True) or 100)
thick_range  = st.sidebar.slider("Thickness (mm)", t_min, t_max, (t_min, t_max))

fdf_samples = df[~df["is_ref"]].copy()
if sample_type == "Single Layer Only": fdf_samples = fdf_samples[~fdf_samples["is_composite"]]
elif sample_type == "Composite Only": fdf_samples = fdf_samples[fdf_samples["is_composite"]]
if trim_sel: fdf_samples = fdf_samples[fdf_samples["trim_level"].isin(trim_sel)]
if sup_sel:  fdf_samples = fdf_samples[fdf_samples["material_supplier"].isin(sup_sel)]
fdf_samples = fdf_samples[fdf_samples[mass_col].between(*mass_range) | fdf_samples[mass_col].isna()]
fdf_samples = fdf_samples[fdf_samples["thickness_mm"].between(*thick_range) | fdf_samples["thickness_mm"].isna()]

fdf_refs = df[df["is_ref"]]
fdf      = pd.concat([fdf_samples, fdf_refs], ignore_index=True)

st.sidebar.markdown("---")
ref_labels       = sorted(fdf[fdf["is_ref"]]["curve_label"].dropna().unique().tolist())
sample_labels  = sorted(fdf[~fdf["is_ref"]]["curve_label"].dropna().unique().tolist())
available_labels = ref_labels + sample_labels

select_all      = st.sidebar.checkbox("Select All Samples", value=False)
selected_labels = st.sidebar.multiselect(
    f"Select Samples ({len(available_labels)} available)",
    available_labels,
    default=available_labels if select_all else []
)

st.sidebar.markdown("<small><span style='color:#a78bfa'>⊕</span> composite — dashed line<br><span style='color:#fbbf24'>★</span> reference — bold gold line</small>", unsafe_allow_html=True)
abs_type = st.sidebar.radio("Measurement Method", ["alpha_cabin", "alpha_kundt"])

all_active_labels = selected_labels
if not all_active_labels:
    st.warning("👈 Select at least one sample from the sidebar to generate the charts.")
    st.stop()

plot_data = fdf[fdf["curve_label"].isin(all_active_labels)]

# ─────────────────────────────────────────────────────────────────
# TABS (Tout le reste du tracé Plotly reste identique)
# ─────────────────────────────────────────────────────────────────
tab1, tab2 = st.tabs(["📈 Interactive Plot", "🗃️ Raw Data & Exports"])

with tab1:
    n_sel_comp = sum(1 for l in all_active_labels if l.startswith("⊕"))
    n_sel_ref  = sum(1 for l in all_active_labels if l.startswith("★"))
    n_sel_sing = len(all_active_labels) - n_sel_comp - n_sel_ref

    col_a, col_b, col_c, col_d = st.columns([1, 1, 1, 3])
    with col_a: st.metric("Samples", n_sel_sing + n_sel_comp)
    with col_b: st.metric("Composite", n_sel_comp)
    with col_c: st.metric("References", n_sel_ref)
    st.markdown("<br>", unsafe_allow_html=True)

    if n_sel_comp > 0:
        comp_stns = [l.split("|")[0].strip().lstrip("⊕").strip() for l in all_active_labels if l.startswith("⊕")]
        st.markdown(f'<div class="composite-info-box"><b>🔀 Composite samples:</b> {", ".join(comp_stns)}<br><span style="opacity:.8">Two superimposed material layers — shown as <b>dashed lines</b>.</span></div>', unsafe_allow_html=True)
    if n_sel_ref > 0:
        ref_names = [l.lstrip("★").strip() for l in all_active_labels if l.lstrip("★").strip() in all_active_labels or True]
        ref_names = [l.lstrip("★").strip() for l in all_active_labels if l.startswith("★")]
        st.markdown(f'<div class="ref-info-box"><b>📌 Reference curves:</b> {", ".join(ref_names)}<br><span style="opacity:.8">Benchmark data — shown as <b>bold gold lines</b>.</span></div>', unsafe_allow_html=True)

    FREQ_TICKS = {
        "alpha_cabin": [315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000],
        "alpha_kundt": [200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300]
    }
    ticks = FREQ_TICKS.get(abs_type, sorted(plot_data["frequency"].dropna().unique()))

    COLORS = ["#1D4ED8", "#E11D48", "#10B981", "#7C3AED", "#EA580C", "#06B6D4", "#EC4899", "#6B7280", "#84CC16", "#A16207", "#4F46E5", "#0F766E"]
    REF_COLOR = "#F59E0B"

    fig = go.Figure()
    color_idx = 0

    for label in all_active_labels:
        sub = plot_data[plot_data["curve_label"] == label].dropna(subset=["frequency", abs_type])
        if sub.empty: continue
        sub = sub.groupby("frequency", as_index=False)[abs_type].mean().sort_values("frequency")

        ref_curve  = label.startswith("★")
        comp_curve = label.startswith("⊕")

        if ref_curve:
            color, line_dash, line_width, marker_sym, marker_sz, hover_tag = REF_COLOR, "solid", 3.5, "star", 10, " <i>(reference)</i>"
        elif comp_curve:
            color, line_dash, line_width, marker_sym, marker_sz, hover_tag = COLORS[color_idx % len(COLORS)], "dash", 2.8, "diamond", 7, " <i>(composite)</i>"; color_idx += 1
        else:
            color, line_dash, line_width, marker_sym, marker_sz, hover_tag = COLORS[color_idx % len(COLORS)], "solid", 2.5, "circle", 6, ""; color_idx += 1

        fig.add_trace(go.Scatter(
            x=sub["frequency"], y=sub[abs_type], mode="lines+markers", name=label,
            line=dict(color=color, width=line_width, dash=line_dash),
            marker=dict(color=color, size=marker_sz, symbol=marker_sym),
            hovertemplate=f"<b>%{{fullData.name}}</b><br>Freq: %{{x}} Hz<br>α: %{{y:.3f}}{hover_tag}<extra></extra>"
        ))

    legend_entries = []
    if n_sel_sing > 0: legend_entries.append(("── Single layer", "solid", "rgba(200,200,200,0.7)"))
    if n_sel_comp > 0: legend_entries.append(("╌╌ Composite (2 layers)", "dash", "rgba(200,200,200,0.7)"))
    if n_sel_ref > 0: legend_entries.append(("── Reference", "solid", REF_COLOR))

    for name, dash, col in legend_entries:
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines", line=dict(color=col, width=2.5 if "Reference" in name else 2, dash=dash), name=name, showlegend=True))

    fig.update_layout(
        title=f"Sound Absorption Coefficients ({abs_type.replace('_', ' ').title()})",
        xaxis=dict(title="Frequency (Hz)", type="log", tickmode="array", tickvals=ticks, ticktext=[str(t) for t in ticks], showgrid=True, gridcolor="rgba(128,128,128,0.2)"),
        yaxis=dict(title="Absorption Coefficient α", range=[-0.05, 1.1], showgrid=True, gridcolor="rgba(128,128,128,0.2)"),
        hovermode="x unified", plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", yanchor="bottom", y=-0.35, xanchor="center", x=0.5), height=640
    )
    st.plotly_chart(fig, use_container_width=True)

with tab2:
    st.markdown("### Screened Dataset")
    extra_cols = [c for c in ["is_ref", "is_composite"] if c in plot_data.columns]
    show_cols  = [c for c in ["stn", "curve_label"] + extra_cols + ["frequency", abs_type] if c in plot_data.columns]
    grp_cols   = ["stn", "curve_label"] + extra_cols + ["frequency"]
    raw_output = plot_data[show_cols].groupby(grp_cols, as_index=False)[abs_type].mean().sort_values(["stn", "frequency"])
    raw_output = raw_output.rename(columns={"is_composite": "Composite", "is_ref": "Reference"})

    st.dataframe(raw_output, use_container_width=True, hide_index=True)
    csv = raw_output.to_csv(index=False).encode("utf-8")
    st.download_button("📥 Export Current Table (.CSV)", csv, "filtered_data.csv", "text/csv")
