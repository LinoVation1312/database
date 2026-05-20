import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import io
import re

# ─────────────────────────────────────────────────────────────────
# CONFIGURATION DE LA PAGE
# ─────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Acoustic DB Explorer V2", page_icon="🔊", layout="wide")

st.markdown("""
<style>
    [data-testid="stSidebar"] { background-color: #0f1117; }
    [data-testid="stSidebar"] * { color: #e8e8e8 !important; }
    .stMetric { background-color: #1e212b; padding: 15px; border-radius: 10px; border: 1px solid #333; }
    h1 { color: #3b82f6 !important; font-weight: 800 !important; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────
# FONCTIONS ASSISTANTES
# ─────────────────────────────────────────────────────────────────
def norm_cols(cols):
    return (cols.str.strip().str.lower()
            .str.replace(r'[^\w\s]', '', regex=True)
            .str.replace(r'\s+', '_', regex=True))

MATERIAL_MAP = [
    (r'\bGlass\s*[Ff]iber\b', 'GF'), (r'\bPANox\b|\bPANOX\b', 'PANox'),
    (r'\bPES\b', 'PES'), (r'\bPET\b', 'PET'), (r'\bPP\b', 'PP')
]

def parse_materials(description: str) -> str:
    if not isinstance(description, str): return "?"
    hits = [(m.start(), label) for pattern, label in MATERIAL_MAP for m in re.finditer(pattern, description, re.IGNORECASE)]
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

def build_curve_label(row: pd.Series, mass_col: str) -> str:
    stn = str(row.get("stn", "?")).strip()
    mat = parse_materials(str(row.get("detailed_description", "")))
    mass = row.get(mass_col)
    thick = row.get("thickness_mm")

    mass_str = f"{int(float(mass))} gsm" if pd.notna(mass) else "? gsm"
    thick_str = f"{thick} mm" if pd.notna(thick) else "? mm"
    airgap = parse_airgap(str(row.get("material_orientation", ""))) or parse_airgap(str(row.get("detailed_description", "")))
    ag_str = f" | AG {airgap}" if airgap else ""

    return f"{stn} | {mat} | {mass_str} | {thick_str}{ag_str}"

# ─────────────────────────────────────────────────────────────────
# CHARGEMENT ET NETTOYAGE
# ─────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner="Analyse du fichier en cours...")
def load_data(file_bytes: bytes):
    buf = io.BytesIO(file_bytes)
    xf = pd.ExcelFile(buf, engine="openpyxl")

    gnrl_sheet = next((s for s in xf.sheet_names if s.strip().upper().startswith("GNRL")), None)
    data_sheet = next((s for s in xf.sheet_names if s.strip().upper() == "DATA"), None)
    
    if not gnrl_sheet or not data_sheet: 
        st.error("❌ Feuilles GNRL ou DATA introuvables.")
        return None, None

    # --- GNRL ---
    raw = pd.read_excel(buf, sheet_name=gnrl_sheet, engine="openpyxl", header=None)
    header_row = next((i for i, r in raw.iterrows() if any("sample" in str(v).lower() or "stn" in str(v).lower() for v in r if pd.notna(v))), None)
    
    if header_row is None:
        st.error("❌ Ligne d'en-tête introuvable dans GNRL.")
        return None, None

    gnrl = pd.read_excel(buf, sheet_name=gnrl_sheet, engine="openpyxl", header=header_row)
    gnrl.columns = norm_cols(gnrl.columns)
    gnrl = gnrl.dropna(how="all")

    # Recherche robuste de la colonne STN dans GNRL
    stn_cols = [c for c in gnrl.columns if "stn" in c or "sample" in c]
    if not stn_cols:
        st.error(f"❌ Colonne STN introuvable dans GNRL. Colonnes vues : {list(gnrl.columns)}")
        return None, None
        
    short_col = next((c for c in stn_cols if gnrl[c].dropna().astype(str).str.strip().str.match(r'^E\d+').mean() > 0.4), stn_cols[-1])
    gnrl = gnrl.rename(columns={short_col: "stn"})
    gnrl["stn"] = gnrl["stn"].astype(str).str.strip().str.upper()
    gnrl = gnrl[gnrl["stn"].str.match(r'^E\d+.*')]

    mass_col = next((c for c in gnrl.columns if "surface_mass" in c), None)
    if mass_col:
        gnrl[mass_col] = pd.to_numeric(gnrl[mass_col], errors="coerce")
    gnrl["thickness_mm"] = pd.to_numeric(gnrl.get("thickness_mm"), errors="coerce")

    # --- DATA ---
    data = pd.read_excel(buf, sheet_name=data_sheet, engine="openpyxl")
    data.columns = norm_cols(data.columns)
    
    # Recherche robuste de la colonne STN dans DATA
    if "stn" not in data.columns:
        stn_data_col = next((c for c in data.columns if "stn" in c or "sample" in c), None)
        if stn_data_col:
            data = data.rename(columns={stn_data_col: "stn"})
        else:
            st.error(f"❌ Colonne STN introuvable dans la feuille DATA. Colonnes vues : {list(data.columns)}")
            return None, None

    # Normalisation des autres colonnes importantes
    for c in data.columns:
        if "alpha_cabin" in c: data = data.rename(columns={c: "alpha_cabin"})
        elif "alpha_kundt" in c: data = data.rename(columns={c: "alpha_kundt"})
        elif "frequency" in c: data = data.rename(columns={c: "frequency"})

    # Remplissage des cases vides (fusion des cellules Excel) et nettoyage
    data["stn"] = data["stn"].astype(str).replace({"nan": pd.NA, "None": pd.NA, "": pd.NA}).ffill().str.strip().str.upper()
    data = data[data["stn"].str.match(r'^E\d+.*', na=False)]

    for col in ["frequency", "alpha_cabin", "alpha_kundt"]:
        if col in data.columns: 
            data[col] = pd.to_numeric(data[col], errors="coerce")

    # --- MERGE ---
    merged = data.merge(gnrl, on="stn", how="left")
    merged["curve_label"] = merged.apply(lambda r: build_curve_label(r, mass_col), axis=1)
    return merged, mass_col

# ─────────────────────────────────────────────────────────────────
# INTERFACE PRINCIPALE
# ─────────────────────────────────────────────────────────────────
st.title("🔊 Plateforme d'Analyse Acoustique")

uploaded_file = st.file_uploader("Chargez le fichier Excel (Database_Vx.xlsx)", type=["xlsx"])

if not uploaded_file:
    st.info("⬆️ Veuillez charger un fichier Excel pour activer le tableau de bord.")
    st.stop()

df, mass_col = load_data(uploaded_file.read())
if df is None:
    st.error("Format de fichier non reconnu ou feuilles manquantes.")
    st.stop()

# --- FILTRES SIDEBAR ---
st.sidebar.header("🎛️ Filtres")
trim_sel = st.sidebar.multiselect("Niveau de Finition", sorted(df["trim_level"].dropna().unique())) if "trim_level" in df.columns else []
sup_sel = st.sidebar.multiselect("Fournisseur", sorted(df["material_supplier"].dropna().unique())) if "material_supplier" in df.columns else []

m_min, m_max = float(df[mass_col].min(skipna=True) or 0), float(df[mass_col].max(skipna=True) or 100)
mass_range = st.sidebar.slider("Masse Surfacique (g/m²)", m_min, m_max, (m_min, m_max))

t_min, t_max = float(df["thickness_mm"].min(skipna=True) or 0), float(df["thickness_mm"].max(skipna=True) or 100)
thick_range = st.sidebar.slider("Épaisseur (mm)", t_min, t_max, (t_min, t_max))

fdf = df.copy()
if trim_sel: fdf = fdf[fdf["trim_level"].isin(trim_sel)]
if sup_sel: fdf = fdf[fdf["material_supplier"].isin(sup_sel)]
fdf = fdf[fdf[mass_col].between(*mass_range) | fdf[mass_col].isna()]
fdf = fdf[fdf["thickness_mm"].between(*thick_range) | fdf["thickness_mm"].isna()]

st.sidebar.markdown("---")
available_labels = sorted(fdf["curve_label"].dropna().unique().tolist())
select_all = st.sidebar.checkbox("Tout sélectionner", value=False)
selected_labels = st.sidebar.multiselect(f"Échantillons ({len(available_labels)})", available_labels, default=available_labels if select_all else [])

abs_type = st.sidebar.radio("Méthode de mesure", ["alpha_cabin", "alpha_kundt"])

if not selected_labels:
    st.warning("👈 Sélectionnez au moins un échantillon pour générer l'analyse.")
    st.stop()

plot_data = fdf[fdf["curve_label"].isin(selected_labels)]

# --- ONGLETS ---
tab1, tab2 = st.tabs(["📈 Graphique Interactif", "🗃️ Données Brutes & Exports"])

with tab1:
    # --- KPIs Sécurisés ---
    k1, k2, k3 = st.columns(3)
    k1.metric("Échantillons comparés", len(selected_labels))
    
    avg_mass = pd.to_numeric(plot_data[mass_col], errors="coerce").mean()
    avg_thick = pd.to_numeric(plot_data['thickness_mm'], errors="coerce").mean()
    
    k2.metric("Masse moyenne", f"{avg_mass:.0f} g/m²" if pd.notna(avg_mass) else "N/A")
    k3.metric("Épaisseur moyenne", f"{avg_thick:.1f} mm" if pd.notna(avg_thick) else "N/A")
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    # PLOTLY FIGURE
    FREQ_TICKS = {
        "alpha_cabin": [315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000],
        "alpha_kundt": [200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300]
    }
    ticks = FREQ_TICKS.get(abs_type, sorted(plot_data["frequency"].dropna().unique()))

    fig = go.Figure()
    for label in selected_labels:
        # CORRECTION BUG E0019 : on groupe par fréquence et on fait la moyenne pour éviter les doublons/zigzags
        sub = plot_data[plot_data["curve_label"] == label].dropna(subset=["frequency", abs_type])
        if sub.empty: continue
        
        sub = sub.groupby("frequency", as_index=False)[abs_type].mean().sort_values("frequency")
        
        fig.add_trace(go.Scatter(
            x=sub["frequency"], y=sub[abs_type],
            mode='lines+markers', name=label,
            hovertemplate="Fréq: %{x} Hz<br>Alpha: %{y:.2f}<extra></extra>"
        ))

    fig.update_layout(
        title=f"Coefficients d'absorption ({abs_type.replace('_', ' ').title()})",
        xaxis=dict(
            title="Fréquence (Hz)", type="log", 
            tickmode='array', tickvals=ticks, ticktext=[str(t) for t in ticks],
            showgrid=True, gridcolor='rgba(128,128,128,0.2)'
        ),
        yaxis=dict(
            title="Coefficient α", range=[-0.05, 1.1],
            showgrid=True, gridcolor='rgba(128,128,128,0.2)'
        ),
        hovermode="x unified",
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        legend=dict(orientation="h", yanchor="bottom", y=-0.3, xanchor="center", x=0.5),
        height=600
    )

    st.plotly_chart(fig, use_container_width=True)

    # Export interactif HTML
    html_bytes = fig.to_html(include_plotlyjs="cdn").encode("utf-8")
    st.download_button("🌐 Télécharger le graphique interactif (Format Web HTML)", data=html_bytes, file_name="graphique_interactif.html", mime="text/html")

with tab2:
    st.markdown("### Aperçu des données sélectionnées")
    show_cols = [c for c in ["stn", "curve_label", "frequency", abs_type] if c in plot_data.columns]
    
    # On nettoie également le tableau de données brutes pour l'export (suppression des doublons)
    raw_output = plot_data[show_cols].groupby(["stn", "curve_label", "frequency"], as_index=False)[abs_type].mean()
    raw_output = raw_output.sort_values(["stn", "frequency"])
    
    st.dataframe(raw_output, use_container_width=True, hide_index=True)
    
    csv = raw_output.to_csv(index=False).encode('utf-8')
    st.download_button("📥 Exporter ces données (.CSV)", csv, "donnes_filtrees.csv", "text/csv")
