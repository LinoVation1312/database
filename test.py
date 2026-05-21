import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import io
import re

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
    .composite-badge {
        display: inline-block;
        background: linear-gradient(135deg, #7c3aed, #3b82f6);
        color: white !important;
        font-size: 0.72em;
        font-weight: 700;
        padding: 2px 8px;
        border-radius: 4px;
        margin-right: 6px;
        letter-spacing: 0.05em;
        vertical-align: middle;
    }
    .composite-info-box {
        background-color: #1a1f2e;
        border-left: 4px solid #7c3aed;
        border-radius: 6px;
        padding: 10px 14px;
        margin-bottom: 12px;
        font-size: 0.85em;
        color: #c4b5fd;
    }
    .legend-composite { border-top: 2px dashed #aaa; display: inline-block; width: 30px; vertical-align: middle; margin-right: 6px; }
    .legend-single    { border-top: 2px solid  #aaa; display: inline-block; width: 30px; vertical-align: middle; margin-right: 6px; }
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

def is_composite(row: pd.Series) -> bool:
    """
    Detect composite samples (two distinct material layers stacked).
    Strategy:
      1. 'COMP' in the STN code (explicit naming convention).
      2. Heuristic: description contains '+' AND both sides of the '+' contain
         at least one recognized material keyword → two different product layers.
    """
    stn  = str(row.get("stn", "")).upper()
    desc = str(row.get("detailed_description", ""))

    if "COMP" in stn:
        return True

    if "+" in desc:
        parts = desc.split("+")
        if len(parts) >= 2:
            mat_in = lambda t: any(
                re.search(pat, t, re.IGNORECASE) for pat, _ in MATERIAL_MAP
            )
            if mat_in(parts[0]) and mat_in("+".join(parts[1:])):
                return True

    return False

def parse_composite_layers(description: str, mass_col_val):
    """
    Return (layer1_label, layer2_label) for composite descriptions.
    E.g. "60% PES 40% PP + compressed 2 mm PET 1400 GSM 20 mm airgap"
         → ("PES+PP  1200 gsm", "PET  1400 gsm  2 mm")
    """
    if not isinstance(description, str):
        return None, None
    parts = description.split("+")
    if len(parts) < 2:
        return None, None

    def layer_info(text, fallback_mass=None):
        mats = parse_materials(text)
        mass_m = re.search(r'(\d[\d,]*)\s*(?:gsm|gm|g/m²|g/m2)', text, re.IGNORECASE)
        mass = mass_m.group(1).replace(",", "") if mass_m else (str(int(float(fallback_mass))) if pd.notna(fallback_mass) else "?")
        thick_m = re.search(r'(\d+(?:[.,]\d+)?)\s*mm', text, re.IGNORECASE)
        thick = thick_m.group(1) if thick_m else None
        label = f"{mats}  {mass} gsm"
        if thick:
            label += f"  {thick} mm"
        return label

    l1 = layer_info(parts[0].strip(), fallback_mass=mass_col_val)
    l2 = layer_info("+".join(parts[1:]).strip())
    return l1, l2

def build_curve_label(row: pd.Series, mass_col: str) -> str:
    stn   = str(row.get("stn", "?")).strip()
    desc  = str(row.get("detailed_description", ""))
    mass  = row.get(mass_col)
    thick = row.get("thickness_mm")

    thick_str = f"{thick} mm" if pd.notna(thick) else "? mm"
    airgap    = parse_airgap(str(row.get("material_orientation", ""))) or parse_airgap(desc)
    ag_str    = f" | AG {airgap}" if airgap else ""

    if is_composite(row):
        l1, l2 = parse_composite_layers(desc, mass)
        if l1 and l2:
            return f"⊕ {stn} | [{l1}] + [{l2}] | {thick_str}{ag_str}"
        # Fallback for composite with unparseable description
        mat      = parse_materials(desc)
        mass_str = f"{int(float(mass))} gsm" if pd.notna(mass) else "? gsm"
        return f"⊕ {stn} | {mat} | {mass_str} | {thick_str}{ag_str}"

    mat      = parse_materials(desc)
    mass_str = f"{int(float(mass))} gsm" if pd.notna(mass) else "? gsm"
    return f"{stn} | {mat} | {mass_str} | {thick_str}{ag_str}"

# ─────────────────────────────────────────────────────────────────
# DATA LOADING & CLEANING
# ─────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner="Analyzing Excel database file...")
def load_data(file_bytes: bytes):
    buf = io.BytesIO(file_bytes)
    xf  = pd.ExcelFile(buf, engine="openpyxl")

    gnrl_sheet = next((s for s in xf.sheet_names if s.strip().upper().startswith("GNRL")), None)
    data_sheet = next((s for s in xf.sheet_names if s.strip().upper() == "DATA"), None)

    if not gnrl_sheet or not data_sheet:
        st.error("❌ Sheets 'GNRL' or 'DATA' not found in the uploaded workbook.")
        return None, None

    # --- GNRL Sheet ---
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

    stn_cols  = [c for c in gnrl.columns if "stn" in c or "sample" in c]
    if not stn_cols:
        st.error(f"❌ STN column not found in GNRL sheet. Available: {list(gnrl.columns)}")
        return None, None

    short_col = next(
        (c for c in stn_cols if gnrl[c].dropna().astype(str).str.strip().str.match(r'^E\d+').mean() > 0.4),
        stn_cols[-1]
    )
    gnrl = gnrl.rename(columns={short_col: "stn"})
    gnrl["stn"] = gnrl["stn"].astype(str).str.strip().str.upper()
    gnrl = gnrl[gnrl["stn"].str.match(r'^E\d+.*')]

    mass_col = next((c for c in gnrl.columns if "surface_mass" in c), None)
    if mass_col:
        gnrl[mass_col] = pd.to_numeric(gnrl[mass_col], errors="coerce")
    gnrl["thickness_mm"] = pd.to_numeric(gnrl.get("thickness_mm"), errors="coerce")

    # Flag composites at GNRL level
    gnrl["is_composite"] = gnrl.apply(is_composite, axis=1)

    # --- DATA Sheet ---
    data = pd.read_excel(buf, sheet_name=data_sheet, engine="openpyxl")
    data.columns = norm_cols(data.columns)

    if "stn" not in data.columns:
        stn_data_col = next((c for c in data.columns if "stn" in c or "sample" in c), None)
        if stn_data_col:
            data = data.rename(columns={stn_data_col: "stn"})
        else:
            st.error(f"❌ STN column not found in DATA sheet. Available: {list(data.columns)}")
            return None, None

    for c in data.columns:
        if "alpha_cabin" in c: data = data.rename(columns={c: "alpha_cabin"})
        elif "alpha_kundt" in c: data = data.rename(columns={c: "alpha_kundt"})
        elif "frequency" in c:  data = data.rename(columns={c: "frequency"})

    data["stn"] = (data["stn"].astype(str)
                   .replace({"nan": pd.NA, "None": pd.NA, "": pd.NA})
                   .ffill().str.strip().str.upper())
    data = data[data["stn"].str.match(r'^E\d+.*', na=False)]

    for col in ["frequency", "alpha_cabin", "alpha_kundt"]:
        if col in data.columns:
            data[col] = pd.to_numeric(data[col], errors="coerce")

    # --- MERGE ---
    merged = data.merge(gnrl, on="stn", how="left")
    merged["is_composite"] = merged["is_composite"].fillna(False)
    merged["curve_label"]  = merged.apply(lambda r: build_curve_label(r, mass_col), axis=1)
    return merged, mass_col

# ─────────────────────────────────────────────────────────────────
# MAIN UI
# ─────────────────────────────────────────────────────────────────
st.title("🔊 DATABASE")

uploaded_file = st.file_uploader("Upload Data File (Database_Vx.xlsx)", type=["xlsx"])

if not uploaded_file:
    st.info("⬆ Please upload an Excel database file to initialize the dashboard.")
    st.stop()

df, mass_col = load_data(uploaded_file.read())
if df is None:
    st.stop()

# ─────────────────────────────────────────────────────────────────
# SIDEBAR FILTERS
# ─────────────────────────────────────────────────────────────────
st.sidebar.header("🎛️ Global Filters")

# --- Composite filter ---
n_comp   = int(df.groupby("stn")["is_composite"].first().sum())
n_single = int((~df.groupby("stn")["is_composite"].first()).sum())
sample_type = st.sidebar.radio(
    "Sample Type",
    ["All", "Single Layer Only", "Composite Only"],
    index=0,
    help=f"{n_comp} composite sample(s) · {n_single} single-layer sample(s)"
)
st.sidebar.markdown("---")

trim_sel = st.sidebar.multiselect("Trim Level", sorted(df["trim_level"].dropna().unique())) if "trim_level" in df.columns else []
sup_sel  = st.sidebar.multiselect("Material Supplier", sorted(df["material_supplier"].dropna().unique())) if "material_supplier" in df.columns else []

m_min, m_max = float(df[mass_col].min(skipna=True) or 0), float(df[mass_col].max(skipna=True) or 100)
mass_range   = st.sidebar.slider("Surface Mass (g/m²)", m_min, m_max, (m_min, m_max))

t_min, t_max = float(df["thickness_mm"].min(skipna=True) or 0), float(df["thickness_mm"].max(skipna=True) or 100)
thick_range  = st.sidebar.slider("Thickness (mm)", t_min, t_max, (t_min, t_max))

# --- Apply filters ---
fdf = df.copy()
if sample_type == "Single Layer Only":
    fdf = fdf[~fdf["is_composite"]]
elif sample_type == "Composite Only":
    fdf = fdf[fdf["is_composite"]]

if trim_sel: fdf = fdf[fdf["trim_level"].isin(trim_sel)]
if sup_sel:  fdf = fdf[fdf["material_supplier"].isin(sup_sel)]
fdf = fdf[fdf[mass_col].between(*mass_range) | fdf[mass_col].isna()]
fdf = fdf[fdf["thickness_mm"].between(*thick_range) | fdf["thickness_mm"].isna()]

st.sidebar.markdown("---")
available_labels = sorted(fdf["curve_label"].dropna().unique().tolist())
select_all       = st.sidebar.checkbox("Select All Samples", value=False)
selected_labels  = st.sidebar.multiselect(
    f"Select Samples ({len(available_labels)} available)",
    available_labels,
    default=available_labels if select_all else []
)
# Legend hint in sidebar
st.sidebar.markdown(
    "<small><span style='color:#a78bfa'>⊕</span> prefix = composite (dashed line)</small>",
    unsafe_allow_html=True
)

abs_type = st.sidebar.radio("Measurement Method", ["alpha_cabin", "alpha_kundt"])

if not selected_labels:
    st.warning("👈 Select at least one sample from the sidebar to generate the charts.")
    st.stop()

plot_data = fdf[fdf["curve_label"].isin(selected_labels)]

# ─────────────────────────────────────────────────────────────────
# TABS
# ─────────────────────────────────────────────────────────────────
tab1, tab2 = st.tabs(["📈 Interactive Plot", "🗃️ Raw Data & Exports"])

with tab1:

    # --- KPI row ---
    n_sel_comp = sum(1 for l in selected_labels if l.startswith("⊕"))
    n_sel_sing = len(selected_labels) - n_sel_comp
    col_a, col_b, col_c = st.columns([1, 1, 4])
    with col_a:
        st.metric("Compared Samples", len(selected_labels))
    with col_b:
        st.metric("Composite", n_sel_comp, help="Samples made of two stacked layers")
    st.markdown("<br>", unsafe_allow_html=True)

    # Info banner if composites are shown
    if n_sel_comp > 0:
        comp_stns = [l.split("|")[0].strip().lstrip("⊕").strip() for l in selected_labels if l.startswith("⊕")]
        st.markdown(
            f'<div class="composite-info-box">'
            f'<b>🔀 Composite samples included:</b> {", ".join(comp_stns)}<br>'
            f'<span style="opacity:.8">These samples consist of two superimposed material layers. '
            f'They are shown as <b>dashed lines</b> in the chart.</span>'
            f'</div>',
            unsafe_allow_html=True
        )

    # --- PLOTLY ---
    FREQ_TICKS = {
        "alpha_cabin": [315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000],
        "alpha_kundt": [200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300]
    }
    ticks = FREQ_TICKS.get(abs_type, sorted(plot_data["frequency"].dropna().unique()))

    COLORS = [
        "#1D4ED8", "#E11D48", "#10B981", "#F59E0B", "#7C3AED",
        "#EA580C", "#06B6D4", "#EC4899", "#6B7280", "#84CC16",
        "#A16207", "#4F46E5"
    ]

    fig = go.Figure()

    for i, label in enumerate(selected_labels):
        sub = plot_data[plot_data["curve_label"] == label].dropna(subset=["frequency", abs_type])
        if sub.empty:
            continue
        sub = sub.groupby("frequency", as_index=False)[abs_type].mean().sort_values("frequency")

        color      = COLORS[i % len(COLORS)]
        composite  = label.startswith("⊕")
        line_dash  = "dash" if composite else "solid"
        line_width = 2.8 if composite else 2.5
        marker_sym = "diamond" if composite else "circle"
        marker_sz  = 7 if composite else 6

        # Clean display name (strip ⊕ prefix for readability in legend)
        display_name = label

        fig.add_trace(go.Scatter(
            x=sub["frequency"], y=sub[abs_type],
            mode="lines+markers",
            name=display_name,
            line=dict(color=color, width=line_width, dash=line_dash),
            marker=dict(color=color, size=marker_sz, symbol=marker_sym),
            hovertemplate=(
                "<b>%{fullData.name}</b><br>"
                "Freq: %{x} Hz<br>"
                "α: %{y:.3f}"
                + (" <i>(composite)</i>" if composite else "")
                + "<extra></extra>"
            )
        ))

    # Invisible traces for the dash-style legend
    if n_sel_comp > 0 and n_sel_sing > 0:
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="lines",
            line=dict(color="rgba(200,200,200,0.7)", width=2, dash="solid"),
            name="── Single layer", showlegend=True
        ))
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="lines",
            line=dict(color="rgba(200,200,200,0.7)", width=2, dash="dash"),
            name="╌╌ Composite (2 layers)", showlegend=True
        ))

    fig.update_layout(
        title=f"Sound Absorption Coefficients ({abs_type.replace('_', ' ').title()})",
        xaxis=dict(
            title="Frequency (Hz)", type="log",
            tickmode="array", tickvals=ticks, ticktext=[str(t) for t in ticks],
            showgrid=True, gridcolor="rgba(128,128,128,0.2)"
        ),
        yaxis=dict(
            title="Absorption Coefficient α", range=[-0.05, 1.1],
            showgrid=True, gridcolor="rgba(128,128,128,0.2)"
        ),
        hovermode="x unified",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", yanchor="bottom", y=-0.35, xanchor="center", x=0.5),
        height=640
    )

    st.plotly_chart(fig, use_container_width=True)

    html_bytes = fig.to_html(include_plotlyjs="cdn").encode("utf-8")
    st.download_button(
        "🌐 Download Interactive Plot (HTML)",
        data=html_bytes, file_name="absorption_chart.html", mime="text/html"
    )

with tab2:
    st.markdown("### Screened Dataset")

    show_cols  = [c for c in ["stn", "curve_label", "is_composite", "frequency", abs_type] if c in plot_data.columns]
    raw_output = (
        plot_data[show_cols]
        .groupby(["stn", "curve_label", "is_composite", "frequency"], as_index=False)[abs_type]
        .mean()
        .sort_values(["stn", "frequency"])
    )

    # Rename for display
    raw_output = raw_output.rename(columns={"is_composite": "Composite"})
    st.dataframe(raw_output, use_container_width=True, hide_index=True)

    csv = raw_output.to_csv(index=False).encode("utf-8")
    st.download_button("📥 Export Current Table (.CSV)", csv, "filtered_data.csv", "text/csv")
