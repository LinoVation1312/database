import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import io
import re

# ─────────────────────────────────────────────────────────────────
# PAGE CONFIGURATION
# ─────────────────────────────────────────────────────────────────
st.set_page_config(page_title="DATABASE — Acoustic Laboratory", page_icon="🔊", layout="wide")

st.markdown("""
<style>
    [data-testid="stSidebar"] { background-color: #0f1117; }
    [data-testid="stSidebar"] * { color: #e8e8e8 !important; }
    .stMetric { background-color: #1e212b; padding: 15px; border-radius: 10px; border: 1px solid #333; }
    h1 { color: #3b82f6 !important; font-weight: 800 !important; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
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
# DATA LOADING & CLEANING
# ─────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner="Analyzing Excel database file...")
def load_data(file_bytes: bytes):
    buf = io.BytesIO(file_bytes)
    xf = pd.ExcelFile(buf, engine="openpyxl")

    gnrl_sheet = next((s for s in xf.sheet_names if s.strip().upper().startswith("GNRL")), None)
    data_sheet = next((s for s in xf.sheet_names if s.strip().upper() == "DATA"), None)
    
    if not gnrl_sheet or not data_sheet: 
        st.error("❌ Sheets 'GNRL' or 'DATA' not found in the uploaded workbook.")
        return None, None

    # --- GNRL Sheet Parsing ---
    raw = pd.read_excel(buf, sheet_name=gnrl_sheet, engine="openpyxl", header=None)
    header_row = next((i for i, r in raw.iterrows() if any("sample" in str(v).lower() or "stn" in str(v).lower() for v in r if pd.notna(v))), None)
    
    if header_row is None:
        st.error("❌ Header row ('Sample Number') could not be identified in the GNRL sheet.")
        return None, None

    gnrl = pd.read_excel(buf, sheet_name=gnrl_sheet, engine="openpyxl", header=header_row)
    gnrl.columns = norm_cols(gnrl.columns)
    gnrl = gnrl.dropna(how="all")

    # Robust STN Column Detection
    stn_cols = [c for c in gnrl.columns if "stn" in c or "sample" in c]
    if not stn_cols:
        st.error(f"❌ STN column not found in GNRL sheet. Available columns: {list(gnrl.columns)}")
        return None, None
        
    short_col = next((c for c in stn_cols if gnrl[c].dropna().astype(str).str.strip().str.match(r'^E\d+').mean() > 0.4), stn_cols[-1])
    gnrl = gnrl.rename(columns={short_col: "stn"})
    gnrl["stn"] = gnrl["stn"].astype(str).str.strip().str.upper()
    gnrl = gnrl[gnrl["stn"].str.match(r'^E\d+.*')]

    mass_col = next((c for c in gnrl.columns if "surface_mass" in c), None)
    if mass_col:
        gnrl[mass_col] = pd.to_numeric(gnrl[mass_col], errors="coerce")
    gnrl["thickness_mm"] = pd.to_numeric(gnrl.get("thickness_mm"), errors="coerce")

    # --- DATA Sheet Parsing ---
    data = pd.read_excel(buf, sheet_name=data_sheet, engine="openpyxl")
    data.columns = norm_cols(data.columns)
    
    # Robust STN Column Detection inside DATA
    if "stn" not in data.columns:
        stn_data_col = next((c for c in data.columns if "stn" in c or "sample" in c), None)
        if stn_data_col:
            data = data.rename(columns={stn_data_col: "stn"})
        else:
            st.error(f"❌ STN column not found in DATA sheet. Available columns: {list(data.columns)}")
            return None, None

    for c in data.columns:
        if "alpha_cabin" in c: data = data.rename(columns={c: "alpha_cabin"})
        elif "alpha_kundt" in c: data = data.rename(columns={c: "alpha_kundt"})
        elif "frequency" in c: data = data.rename(columns={c: "frequency"})

    # Forward fill for merged cells optimization
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
# MAIN USER INTERFACE
# ─────────────────────────────────────────────────────────────────
st.title("🔊 DATABASE")

uploaded_file = st.file_uploader("Upload Laboratory Data File (Database_Vx.xlsx)", type=["xlsx"])

if not uploaded_file:
    st.info("⬆️ Please upload an Excel database file to initialize the laboratory dashboard.")
    st.stop()

df, mass_col = load_data(uploaded_file.read())
if df is None:
    st.stop()

# --- SIDEBAR CONTROLS ---
st.sidebar.header("🎛️ Global Filters")
trim_sel = st.sidebar.multiselect("Trim Level", sorted(df["trim_level"].dropna().unique())) if "trim_level" in df.columns else []
sup_sel = st.sidebar.multiselect("Material Supplier", sorted(df["material_supplier"].dropna().unique())) if "material_supplier" in df.columns else []

m_min, m_max = float(df[mass_col].min(skipna=True) or 0), float(df[mass_col].max(skipna=True) or 100)
mass_range = st.sidebar.slider("Surface Mass (g/m²)", m_min, m_max, (m_min, m_max))

t_min, t_max = float(df["thickness_mm"].min(skipna=True) or 0), float(df["thickness_mm"].max(skipna=True) or 100)
thick_range = st.sidebar.slider("Thickness (mm)", t_min, t_max, (t_min, t_max))

fdf = df.copy()
if trim_sel: fdf = fdf[fdf["trim_level"].isin(trim_sel)]
if sup_sel: fdf = fdf[fdf["material_supplier"].isin(sup_sel)]
fdf = fdf[fdf[mass_col].between(*mass_range) | fdf[mass_col].isna()]
fdf = fdf[fdf["thickness_mm"].between(*thick_range) | fdf["thickness_mm"].isna()]

st.sidebar.markdown("---")
available_labels = sorted(fdf["curve_label"].dropna().unique().tolist())
select_all = st.sidebar.checkbox("Select All Samples", value=False)
selected_labels = st.sidebar.multiselect(f"Select Samples ({len(available_labels)} available)", available_labels, default=available_labels if select_all else [])

abs_type = st.sidebar.radio("Measurement Method", ["alpha_cabin", "alpha_kundt"])

if not selected_labels:
    st.warning("👈 Select at least one sample from the sidebar to generate the laboratory charts.")
    st.stop()

plot_data = fdf[fdf["curve_label"].isin(selected_labels)]

# --- INTERACTIVE TABS ---
tab1, tab2 = st.tabs(["📈 Interactive Plot", "🗃️ Raw Laboratory Data & Exports"])

with tab1:
    # --- SECURE KPIs ---
    k1, k2, k3 = st.columns(3)
    k1.metric("Compared Samples", len(selected_labels))
    
    avg_mass = pd.to_numeric(plot_data[mass_col], errors="coerce").mean()
    avg_thick = pd.to_numeric(plot_data['thickness_mm'], errors="coerce").mean()
    
    k2.metric("Mean Surface Mass", f"{avg_mass:.0f} g/m²" if pd.notna(avg_mass) else "N/A")
    k3.metric("Mean Thickness", f"{avg_thick:.1f} mm" if pd.notna(avg_thick) else "N/A")
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    # --- PLOTLY CONFIGURATION ---
    FREQ_TICKS = {
        "alpha_cabin": [315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000],
        "alpha_kundt": [200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300]
    }
    ticks = FREQ_TICKS.get(abs_type, sorted(plot_data["frequency"].dropna().unique()))

    # Professional laboratory color palette
    COLORS = ["#1E40AF", "#991B1B", "#065F46", "#854D0E", "#5B21B6", "#0891B2", "#BE185D", "#111827", "#7C2D12"]

    fig = go.Figure()
    for i, label in enumerate(selected_labels):
        # Multi-row duplication safety patch (e.g., E0019)
        sub = plot_data[plot_data["curve_label"] == label].dropna(subset=["frequency", abs_type])
        if sub.empty: continue
        
        sub = sub.groupby("frequency", as_index=False)[abs_type].mean().sort_values("frequency")
        
        # Explicit line/marker color definition to force persistent colors on HTML download
        fig.add_trace(go.Scatter(
            x=sub["frequency"], y=sub[abs_type],
            mode='lines+markers', 
            name=label,
            line=dict(color=COLORS[i % len(COLORS)], width=2.5),
            marker=dict(color=COLORS[i % len(COLORS)], size=6),
            hovertemplate="Freq: %{x} Hz<br>Alpha: %{y:.2f}<extra></extra>"
        ))

    fig.update_layout(
        title=f"Sound Absorption Coefficients ({abs_type.replace('_', ' ').title()})",
        xaxis=dict(
            title="Frequency (Hz)", type="log", 
            tickmode='array', tickvals=ticks, ticktext=[str(t) for t in ticks],
            showgrid=True, gridcolor='rgba(128,128,128,0.2)'
        ),
        yaxis=dict(
            title="Absorption Coefficient α", range=[-0.05, 1.1],
            showgrid=True, gridcolor='rgba(128,128,128,0.2)'
        ),
        hovermode="x unified",
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        legend=dict(orientation="h", yanchor="bottom", y=-0.3, xanchor="center", x=0.5),
        height=620
    )

    st.plotly_chart(fig, use_container_width=True)

    # Standalone HTML Interactive Plot Export
    html_bytes = fig.to_html(include_plotlyjs="cdn").encode("utf-8")
    st.download_button("🌐 Download Interactive Plot (HTML Web Format)", data=html_bytes, file_name="acoustic_absorption_chart.html", mime="text/html")

with tab2:
    st.markdown("### Screened Laboratory Dataset")
    show_cols = [c for c in ["stn", "curve_label", "frequency", abs_type] if c in plot_data.columns]
    
    # Clean export table
    raw_output = plot_data[show_cols].groupby(["stn", "curve_label", "frequency"], as_index=False)[abs_type].mean()
    raw_output = raw_output.sort_values(["stn", "frequency"])
    
    st.dataframe(raw_output, use_container_width=True, hide_index=True)
    
    csv = raw_output.to_csv(index=False).encode('utf-8')
    st.download_button("📥 Export Current Table (.CSV)", csv, "filtered_acoustic_data.csv", "text/csv")
