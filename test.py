import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import io
import re
import requests
import base64

# ─────────────────────────────────────────────────────────────────
# GITHUB CONFIGURATION
# ─────────────────────────────────────────────────────────────────
GITHUB_USER = "LinoVation1312"
GITHUB_REPO = "database"
BRANCH = "master"

# Fetching the secure token from Streamlit Cloud Secrets
if "GITHUB_TOKEN" in st.secrets:
    TOKEN = st.secrets["GITHUB_TOKEN"]
else:
    TOKEN = None

# Headers for GitHub API
headers = {"Authorization": f"token {TOKEN}", "Accept": "application/vnd.github.v3+json"} if TOKEN else {}

# Flexible regex rule for file validation (case-insensitive)
FILENAME_REGEX = r"^database[-_\s]?v.*\.xlsx$"

# ─────────────────────────────────────────────────────────────────
# GITHUB SEARCH / READ / WRITE FUNCTIONS
# ─────────────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def find_and_download_current_file():
    contents_url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/"
    try:
        res = requests.get(contents_url, headers=headers, params={"t": pd.Timestamp.now().timestamp()})
        if res.status_code == 200:
            files = res.json()
            for f in files:
                if re.match(FILENAME_REGEX, f["name"].lower()):
                    file_res = requests.get(f["download_url"], headers=headers, params={"t": pd.Timestamp.now().timestamp()})
                    if file_res.status_code == 200:
                        return f["name"], file_res.content
        return None, None
    except Exception as e:
        return None, None

def upload_new_excel_to_github(new_filename, file_bytes):
    if not TOKEN:
        st.error("❌ The GITHUB_TOKEN is missing from the application secrets.")
        return False

    contents_url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/"
    
    try:
        existing_sha = None
        old_filename_to_delete = None

        res = requests.get(contents_url, headers=headers, params={"t": pd.Timestamp.now().timestamp()})
        if res.status_code == 200:
            for f in res.json():
                if re.match(FILENAME_REGEX, f["name"].lower()):
                    if f["name"].lower() == new_filename.lower():
                        existing_sha = f["sha"]
                        new_filename = f["name"]
                    else:
                        old_filename_to_delete = f["name"]
                        old_file_sha = f["sha"]

        upload_url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{new_filename}"
        content_b64 = base64.b64encode(file_bytes).decode("utf-8")
        
        put_data = {
            "message": f"Database update: {new_filename} via Streamlit",
            "content": content_b64,
            "branch": BRANCH
        }
        if existing_sha:
            put_data["sha"] = existing_sha

        put_res = requests.put(upload_url, headers=headers, json=put_data)
        
        if put_res.status_code not in [200, 201]:
            st.error(f"❌ GitHub push failed (Code {put_res.status_code}): {put_res.text}")
            return False

        if old_filename_to_delete:
            delete_url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{old_filename_to_delete}"
            del_data = {
                "message": f"Cleanup old version {old_filename_to_delete} after upgrade",
                "sha": old_file_sha,
                "branch": BRANCH
            }
            requests.delete(delete_url, headers=headers, json=del_data)

        return True

    except Exception as e:
        st.error(f"Error during GitHub synchronization: {e}")
        return False

# ─────────────────────────────────────────────────────────────────
# PAGE CONFIGURATION (Light Theme applied)
# ─────────────────────────────────────────────────────────────────
st.set_page_config(page_title="DATABASE", page_icon="🔊", layout="wide")

st.markdown("""
<style>
    [data-testid="stSidebar"] { background-color: #f8fafc; border-right: 1px solid #e2e8f0; }
    [data-testid="stSidebar"] * { color: #0f172a !important; }
    [data-testid="stSidebar"] button { background-color: #ffffff !important; border: 1px solid #cbd5e1 !important; color: #0f172a !important; }
    .stMetric { background-color: #f1f5f9; padding: 15px; border-radius: 10px; border: 1px solid #e2e8f0; width: fit-content; }
    .stMetric * { color: #0f172a !important; }
    h1 { color: #1e40af !important; font-weight: 800 !important; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS (Parsing logic)
# ─────────────────────────────────────────────────────────────────
def norm_cols(cols):
    return (cols.str.strip().str.lower().str.replace(r'[^\w\s]', '', regex=True).str.replace(r'\s+', '_', regex=True))

MATERIAL_MAP = [(r'\bGlass\s*[Ff]iber\b', 'GF'), (r'\bPANox\b|\bPANOX\b', 'PANox'), (r'\bPES\b', 'PES'), (r'\bPET\b', 'PET'), (r'\bPP\b', 'PP')]
STN_PATTERN = r'^(E\d+|REF\s+\S.*)$'

def parse_materials(description: str) -> str:
    if not isinstance(description, str): return "?"
    hits = [(m.start(), label) for pattern, label in MATERIAL_MAP for m in re.finditer(pattern, description, re.IGNORECASE)]
    hits.sort(key=lambda x: x[0])
    seen, found = set(), []
    for _, label in hits:
        if label not in seen: seen.add(label); found.append(label)
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
        mass = (mass_m.group(1).replace(",", "") if mass_m else (str(int(float(fallback_mass))) if pd.notna(fallback_mass) else "?"))
        thick_m = re.search(r'(\d+(?:[.,]\d+)?)\s*mm', text, re.IGNORECASE)
        thick = thick_m.group(1) if thick_m else None
        label = f"{mats}  {mass} gsm"
        if thick: label += f"  {thick} mm"
        return label

    l1 = layer_info(parts[0].strip(), fallback_mass=mass_col_val)
    l2 = layer_info("+".join(parts[1:]).strip())
    return l1, l2

def build_curve_label(row: pd.Series, mass_col: str) -> str:
    stn  = str(row.get("stn", "?")).strip()
    desc = str(row.get("detailed_description", ""))
    mass = row.get(mass_col)
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
# DATA LOADING & CLEANING
# ─────────────────────────────────────────────────────────────────
def load_data(file_bytes: bytes):
    buf = io.BytesIO(file_bytes)
    xf  = pd.ExcelFile(buf, engine="openpyxl")

    gnrl_sheet = next((s for s in xf.sheet_names if s.strip().upper().startswith("GNRL")), None)
    abs_sheet  = next((s for s in xf.sheet_names if s.strip().upper() == "ABSORPTION"), None)
    stl_sheet  = next((s for s in xf.sheet_names if s.strip().upper() == "STL"), None)

    if not gnrl_sheet:
        st.error("❌ The 'GNRL' sheet could not be found.")
        return None, None, None

    if not abs_sheet and not stl_sheet:
        st.error("❌ Neither 'ABSORPTION' nor 'STL' sheets could be found.")
        return None, None, None

    raw = pd.read_excel(buf, sheet_name=gnrl_sheet, engine="openpyxl", header=None)
    header_row = next((i for i, r in raw.iterrows() if any("sample" in str(v).lower() or "stn" in str(v).lower() for v in r if pd.notna(v))), None)
    if header_row is None: return None, None, None

    gnrl = pd.read_excel(buf, sheet_name=gnrl_sheet, engine="openpyxl", header=header_row)
    gnrl.columns = norm_cols(gnrl.columns)
    gnrl = gnrl.dropna(how="all")

    stn_cols = [c for c in gnrl.columns if "stn" in c or "sample" in c]
    if not stn_cols: return None, None, None

    short_col = next((c for c in stn_cols if gnrl[c].dropna().astype(str).str.strip().str.match(r'^E\d+').mean() > 0.4), stn_cols[-1])
    gnrl = gnrl.rename(columns={short_col: "stn"})
    gnrl["stn"] = gnrl["stn"].astype(str).str.strip().str.upper()
    gnrl = gnrl[gnrl["stn"].str.match(STN_PATTERN, na=False)]

    mass_col = next((c for c in gnrl.columns if "surface_mass" in c), None)
    if mass_col: gnrl[mass_col] = pd.to_numeric(gnrl[mass_col], errors="coerce")
    gnrl["thickness_mm"] = pd.to_numeric(gnrl.get("thickness_mm"), errors="coerce")

    gnrl["is_ref"]       = gnrl.apply(is_ref, axis=1)
    gnrl["is_composite"] = gnrl.apply(is_composite, axis=1)

    def process_data_sheet(sheet_name):
        if not sheet_name: return pd.DataFrame()
        data = pd.read_excel(buf, sheet_name=sheet_name, engine="openpyxl")
        data.columns = norm_cols(data.columns)

        if "stn" not in data.columns:
            stn_data_col = next((c for c in data.columns if "stn" in c or "sample" in c), None)
            if stn_data_col: data = data.rename(columns={stn_data_col: "stn"})
            else: return pd.DataFrame()

        for c in data.columns:
            # Important to check STL specifically first to not clash with plain alpha_cabin
            if "stl" in c or "alpha_cabin_stl" in c: data = data.rename(columns={c: "alpha_cabin_stl"})
            elif "alpha_cabin" in c: data = data.rename(columns={c: "alpha_cabin"})
            elif "alpha_kundt" in c: data = data.rename(columns={c: "alpha_kundt"})
            elif "frequency"   in c: data = data.rename(columns={c: "frequency"})

        data["stn"] = (data["stn"].astype(str).replace({"nan": pd.NA, "None": pd.NA, "": pd.NA}).ffill().str.strip().str.upper())
        data = data[data["stn"].str.match(STN_PATTERN, na=False)]

        for col in ["frequency", "alpha_cabin", "alpha_kundt", "alpha_cabin_stl"]:
            if col in data.columns: data[col] = pd.to_numeric(data[col], errors="coerce")

        merged = data.merge(gnrl, on="stn", how="left")
        merged["is_ref"]       = merged["is_ref"].fillna(False)
        merged["is_composite"] = merged["is_composite"].fillna(False)
        merged["curve_label"]  = merged.apply(lambda r: build_curve_label(r, mass_col), axis=1)
        return merged

    df_abs = process_data_sheet(abs_sheet)
    df_stl = process_data_sheet(stl_sheet)

    return df_abs, df_stl, mass_col

# ─────────────────────────────────────────────────────────────────
# MAIN UI & DYNAMIC LOADING
# ─────────────────────────────────────────────────────────────────
st.title("🔊 DATABASE")

current_filename, excel_data = find_and_download_current_file()

with st.sidebar.expander("🔄 GitHub Administration", expanded=False):
    uploaded_file = st.file_uploader("Upload a new database file", type=["xlsx"])
    if uploaded_file:
        file_bytes = uploaded_file.read()
        filename_uploaded = uploaded_file.name
        
        if not re.match(FILENAME_REGEX, filename_uploaded.lower()):
            st.error("⚠️ Invalid filename!")
        else:
            if st.button("🚀 Overwrite & Publish Version"):
                with st.spinner("Uploading to GitHub..."):
                    if upload_new_excel_to_github(filename_uploaded, file_bytes):
                        st.success(f"✅ Successfully deployed: {filename_uploaded}")
                        st.cache_data.clear()
                        import time
                        time.sleep(1.5)
                        st.rerun()

if excel_data is None:
    st.error("❌ No valid database file was found on GitHub.")
    st.stop()

st.caption(f"📂 Active database loaded from GitHub: `{current_filename}`")

df_abs, df_stl, mass_col = load_data(excel_data)
if df_abs is None: st.stop()

# ─────────────────────────────────────────────────────────────────
# SIDEBAR FILTERS
# ─────────────────────────────────────────────────────────────────
st.sidebar.header("📁 Data Category")
data_options = []
if not df_abs.empty: data_options.append("Absorption")
if not df_stl.empty: data_options.append("STL")

if not data_options:
    st.error("❌ No valid data found in the 'ABSORPTION' or 'STL' sheets.")
    st.stop()

data_type = st.sidebar.radio("Select Category to Analyze", data_options)

st.sidebar.header("🎛️ Global Filters")

# Dynamically select df based on user choice
if data_type == "Absorption":
    df = df_abs
    available_methods = [c for c in ["alpha_cabin", "alpha_kundt"] if c in df.columns]
    abs_type = st.sidebar.radio("Measurement Method", available_methods) if available_methods else None
    y_range_limit = [-0.05, 1.1]
    y_title_default = "Absorption Coefficient α"
    main_title_default = f"Sound Absorption Coefficients ({abs_type.replace('_', ' ').title()})" if abs_type else "Sound Absorption"
else:
    df = df_stl
    available_methods = [c for c in ["alpha_cabin_stl"] if c in df.columns]
    abs_type = st.sidebar.radio("Measurement Method", available_methods) if available_methods else "alpha_cabin_stl"
    y_range_limit = None # Let Plotly auto-scale based on dB outputs
    y_title_default = "Sound Transmission Loss (dB)"
    main_title_default = f"Sound Transmission Loss (STL)"

n_comp   = int(df[~df["is_ref"]].groupby("stn")["is_composite"].first().sum())
n_single = int((~df[~df["is_ref"]].groupby("stn")["is_composite"].first()).sum())
sample_type = st.sidebar.radio("Sample Type", ["All", "Single Layer Only", "Composite Only"], index=0)
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
sample_labels    = sorted(fdf[~fdf["is_ref"]]["curve_label"].dropna().unique().tolist())
available_labels = ref_labels + sample_labels

select_all      = st.sidebar.checkbox("Select All Samples", value=False)
selected_labels = st.sidebar.multiselect(f"Select Samples ({len(available_labels)} available)", available_labels, default=available_labels if select_all else [])

st.sidebar.markdown("<small><span style='color:#7c3aed'>⊕</span> composite<br><span style='color:#d97706'>★</span> reference</small>", unsafe_allow_html=True)

all_active_labels = selected_labels

with st.sidebar.expander("🏆 Ranking vs Reference", expanded=False):
    if available_labels:
        target_ref = st.selectbox("Select Target", options=["-- Select --"] + available_labels)
        if target_ref != "-- Select --" and abs_type:
            if st.button("Run Analysis"):
                ref_data = fdf[(fdf["curve_label"] == target_ref) & (fdf["frequency"] <= 2000)].dropna(subset=["frequency", abs_type])
                ref_data = ref_data.groupby("frequency", as_index=False)[abs_type].mean()
                ref_dict = dict(zip(ref_data["frequency"], ref_data[abs_type]))
                
                always_above, ranking = [], []
                candidates = [l for l in available_labels if l != target_ref]
                for cand in candidates:
                    cand_data = fdf[(fdf["curve_label"] == cand) & (fdf["frequency"] <= 2000)].dropna(subset=["frequency", abs_type])
                    if cand_data.empty: continue
                    cand_data = cand_data.groupby("frequency", as_index=False)[abs_type].mean()
                    
                    weighted_diffs, sum_weights, is_always_above = [], 0, True
                    for _, row in cand_data.iterrows():
                        freq, val = row["frequency"], row[abs_type]
                        if freq in ref_dict:
                            diff = val - ref_dict[freq]
                            weight = 2000.0 / freq
                            weighted_diffs.append(diff * weight)
                            sum_weights += weight
                            if diff < 0: is_always_above = False
                    
                    if weighted_diffs and sum_weights > 0:
                        avg_diff = sum(weighted_diffs) / sum_weights
                        data_dict = {"Sample": cand, f"Weighted Score": round(avg_diff, 4)}
                        if is_always_above: always_above.append(data_dict)
                        ranking.append(data_dict)
                
                if always_above:
                    st.success("✅ Samples consistently above (or equal to) the reference up to 2 kHz:")
                    st.dataframe(pd.DataFrame(always_above).sort_values(by="Weighted Score", ascending=False), hide_index=True)
                else:
                    st.info("No sample outperforms the reference across the entire frequency range.")
                    if ranking:
                        st.dataframe(pd.DataFrame(ranking).sort_values(by="Weighted Score", ascending=False).head(5), hide_index=True)

if not all_active_labels:
    st.warning("👈 Select at least one sample from the sidebar to generate the charts.")
    st.stop()

plot_data = fdf[fdf["curve_label"].isin(all_active_labels)]

# ─────────────────────────────────────────────────────────────────
# TABS & PLOT & DIRECT DATA EXPORT
# ─────────────────────────────────────────────────────────────────
tab1, tab2 = st.tabs(["📈 Interactive Plot", "🗃️ Raw Data & Exports"])

with tab1:
    import plotly.io as pio

    n_sel_comp = sum(1 for l in all_active_labels if l.startswith("⊕"))
    n_sel_ref  = sum(1 for l in all_active_labels if l.startswith("★"))
    n_sel_sing = len(all_active_labels) - n_sel_comp - n_sel_ref

    col_a, col_b, col_c = st.columns([1, 1, 1])
    with col_a: st.metric("Samples", n_sel_sing + n_sel_comp)
    with col_b: st.metric("Composite", n_sel_comp)
    with col_c: st.metric("References", n_sel_ref)
    st.markdown("<br>", unsafe_allow_html=True)

    with st.expander("🛠️ Display & HTML Export Customization", expanded=True):
        st.info("💡 **HTML Export Tip:** Set your line widths, markers, and tick settings below. Once you export the chart as HTML, you can still edit the titles by clicking directly on them!")
        
        c1, c2, c3 = st.columns(3)
        with c1: main_title = st.text_input("Main Title", main_title_default)
        with c2: x_title = st.text_input("X-Axis Title", "Frequency (Hz)")
        with c3: y_title = st.text_input("Y-Axis Title", y_title_default)

        c4, c5, c6 = st.columns(3)
        with c4: custom_lw = st.slider("Global Line Width", 1.0, 5.0, 2.5, 0.5)
        with c5: custom_ms = st.slider("Global Marker Size", 0, 15, 6, 1)
        with c6:
            show_grid = st.checkbox("Show Grid", value=True)
            tick_density = st.radio("X-Axis Tick Density", ["Standard", "Detailed"], horizontal=True)

        st.markdown("#### 🖌️ Curve Styles")
        style1, style2, style3 = st.columns(3)
        
        line_styles = ["solid", "dash", "dot", "dashdot", "longdash", "longdashdot"]
        marker_styles = ["circle", "square", "diamond", "cross", "x", "triangle-up", "pentagon", "hexagram", "star", "diamond-wide", "none"]

        with style1:
            st.markdown("**★ Reference**")
            ref_dash = st.selectbox("Line Style (Ref)", line_styles, index=0) # solid
            ref_marker = st.selectbox("Marker (Ref)", marker_styles, index=8) # star

        with style2:
            st.markdown("**⊕ Composite**")
            comp_dash = st.selectbox("Line Style (Comp)", line_styles, index=1) # dash
            comp_marker = st.selectbox("Marker (Comp)", marker_styles, index=2) # diamond

        with style3:
            st.markdown("**Single Layer**")
            sing_dash = st.selectbox("Line Style (Single)", line_styles, index=0) # solid
            sing_marker = st.selectbox("Marker (Single)", marker_styles, index=0) # circle

    FREQ_TICKS = {
        "alpha_cabin": [315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000],
        "alpha_kundt": [200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300],
        "alpha_cabin_stl": [315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12700]
    }
    
    xaxis_dict = dict(title=x_title, type="log", showgrid=show_grid, gridcolor="#e2e8f0")

    if tick_density == "Standard" and abs_type in FREQ_TICKS:
        ticks = FREQ_TICKS[abs_type]
        xaxis_dict.update(dict(tickmode="array", tickvals=ticks, ticktext=[str(int(t)) for t in ticks]))
    else:
        # Detailed Grid: Display every single frequency logged to prevent Plotly from hiding points.
        ticks = sorted(plot_data["frequency"].dropna().unique())
        xaxis_dict.update(dict(tickmode="array", tickvals=ticks, ticktext=[str(int(t)) for t in ticks], tickangle=-45))

    fig = go.Figure()
    color_idx = 0
    COLORS = ["#1D4ED8", "#E11D48", "#10B981", "#7C3AED", "#EA580C", "#06B6D4", "#EC4899", "#6B7280", "#84CC16", "#A16207", "#4F46E5", "#0F766E"]
    REF_COLOR = "#D97706"

    for label in all_active_labels:
        sub = plot_data[plot_data["curve_label"] == label].dropna(subset=["frequency", abs_type])
        if sub.empty: continue
        sub = sub.groupby("frequency", as_index=False)[abs_type].mean().sort_values("frequency")

        ref_curve  = label.startswith("★")
        comp_curve = label.startswith("⊕")

        if ref_curve:
            color, line_dash, line_width, marker_sym, marker_sz, hover_tag = REF_COLOR, ref_dash, custom_lw + 1.0, ref_marker, custom_ms + 4, " <i>(reference)</i>"
        elif comp_curve:
            color, line_dash, line_width, marker_sym, marker_sz, hover_tag = COLORS[color_idx % len(COLORS)], comp_dash, custom_lw, comp_marker, custom_ms + 1, " <i>(composite)</i>"; color_idx += 1
        else:
            color, line_dash, line_width, marker_sym, marker_sz, hover_tag = COLORS[color_idx % len(COLORS)], sing_dash, custom_lw, sing_marker, custom_ms, ""; color_idx += 1

        show_markers = marker_sym != "none" and custom_ms > 0

        # Adjust formatting based on output type
        val_format = "%.1f" if data_type == "STL" else "%.3f"
        
        fig.add_trace(go.Scatter(
            x=sub["frequency"], y=sub[abs_type], 
            mode="lines+markers" if show_markers else "lines", 
            name=label,
            line=dict(color=color, width=line_width, dash=line_dash), 
            marker=dict(color=color, size=marker_sz, symbol=marker_sym),
            hovertemplate=f"<b>%{{fullData.name}}</b><br>Freq: %{{x}} Hz<br>Value: %{{y:{val_format}}}{hover_tag}<extra></extra>"
        ))

    fig.update_layout(
        title=main_title,
        xaxis=xaxis_dict,
        yaxis=dict(title=y_title, showgrid=show_grid, gridcolor="#e2e8f0"),
        hovermode="x unified", plot_bgcolor="#ffffff", paper_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", yanchor="bottom", y=-0.45, xanchor="center", x=0.5), height=640
    )
    
    if y_range_limit:
        fig.update_layout(yaxis=dict(range=y_range_limit))

    plotly_config = {
        'editable': True,
        'edits': {
            'titleText': True,
            'axisTitleText': True,
            'legendText': True
        },
        'displayModeBar': True
    }

    st.plotly_chart(fig, width="stretch", config=plotly_config)

    html_bytes = pio.to_html(fig, include_plotlyjs="cdn", full_html=True, config=plotly_config).encode("utf-8")
    st.download_button(
        label="📥 Download Chart (Interactive HTML)",
        data=html_bytes,
        file_name=f"{data_type.lower()}_curves.html",
        mime="text/html",
    )

with tab2:
    st.markdown("### 📥 Source File Download")
    st.download_button(
        label=f"🟢 Download Current Complete Excel File ({current_filename})",
        data=excel_data,
        file_name=current_filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    st.markdown("---")

    st.markdown("### 📊 Formatted Data for Export")
    
    # Préparation des données (Pivot Table)
    pivot_data = plot_data.groupby(['frequency', 'curve_label'], as_index=False)[abs_type].mean()
    wide_df = pivot_data.pivot(index="frequency", columns="curve_label", values=abs_type).reset_index()
    wide_df.columns.name = None
    wide_df = wide_df.rename(columns={"frequency": "Frequency (Hz)"})
    
    # Affichage interactif de la table
    st.dataframe(wide_df, use_container_width=True, hide_index=True)
    
    st.markdown("### 📋 Copy to Excel")
    st.info("💡 **Click the 'Copy' icon in the top right corner of the block below**, then paste it directly into your Excel file.")
    
    # Workaround pour le "Copy to Clipboard" : conversion en texte séparé par des tabulations (format Excel)
    tsv_data = wide_df.to_csv(index=False, sep='\t')
    st.code(tsv_data, language="text")
    
    # Génération Excel Directe avec cellules formatées
    output_excel = io.BytesIO()
    with pd.ExcelWriter(output_excel, engine='openpyxl') as writer:
        wide_df.to_excel(writer, index=False, sheet_name=f'{data_type}_Data')
        worksheet = writer.sheets[f'{data_type}_Data']
        
        # Ajustement automatique de la largeur des colonnes
        for col in worksheet.columns:
            max_length = 0
            column_letter = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            worksheet.column_dimensions[column_letter].width = max_length + 2

    excel_bytes = output_excel.getvalue()
    
    st.download_button(
        label="📥 Direct Export to Excel (.xlsx)",
        data=excel_bytes,
        file_name=f"{data_type.lower()}_data_export.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
