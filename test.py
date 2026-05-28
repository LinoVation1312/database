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
# Validates: Database_V1, database_v2, DATABASE-V3, Database V4, databasev5...
FILENAME_REGEX = r"^database[-_\s]?v.*\.xlsx$"

# ─────────────────────────────────────────────────────────────────
# GITHUB SEARCH / READ / WRITE FUNCTIONS
# ─────────────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def find_and_download_current_file():
    """Automatically searches for a database file on GitHub (flexible on the name) and downloads it"""
    contents_url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/"
    try:
        # Dynamic URL parameter to bypass GitHub API's aggressive caching
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
    """Updates or creates the new file on GitHub, correctly managing the SHA"""
    if not TOKEN:
        st.error("❌ The GITHUB_TOKEN is missing from the application secrets.")
        return False

    contents_url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/"
    
    try:
        existing_sha = None
        old_filename_to_delete = None

        # 1. List files to see if a database file already exists
        res = requests.get(contents_url, headers=headers, params={"t": pd.Timestamp.now().timestamp()})
        if res.status_code == 200:
            for f in res.json():
                if re.match(FILENAME_REGEX, f["name"].lower()):
                    if f["name"].lower() == new_filename.lower():
                        # Same name! Fetch SHA to overwrite it directly
                        existing_sha = f["sha"]
                        new_filename = f["name"] # Keep the existing file's casing if identical
                    else:
                        # Different name (e.g., V1 to V2), keep track to delete it AFTER the upload
                        old_filename_to_delete = f["name"]
                        old_file_sha = f["sha"]

        # 2. Upload / Overwrite target file
        upload_url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{new_filename}"
        content_b64 = base64.b64encode(file_bytes).decode("utf-8")
        
        put_data = {
            "message": f"Database update: {new_filename} via Streamlit",
            "content": content_b64,
            "branch": BRANCH
        }
        # If the file already existed under the same name, pass the required SHA
        if existing_sha:
            put_data["sha"] = existing_sha

        put_res = requests.put(upload_url, headers=headers, json=put_data)
        
        if put_res.status_code not in [200, 201]:
            st.error(f"❌ GitHub push failed (Code {put_res.status_code}): {put_res.text}")
            return False

        # 3. Clean up the old file if the new file was successfully deployed with a different name
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
    /* Sidebar Style - Light Theme */
    [data-testid="stSidebar"] { background-color: #f8fafc; border-right: 1px solid #e2e8f0; }
    [data-testid="stSidebar"] * { color: #0f172a !important; }
    
    /* Sidebar interactive elements and buttons */
    [data-testid="stSidebar"] button { background-color: #ffffff !important; border: 1px solid #cbd5e1 !important; color: #0f172a !important; }
    
    /* Metrics UI boxes */
    .stMetric { background-color: #f1f5f9; padding: 15px; border-radius: 10px; border: 1px solid #e2e8f0; width: fit-content; }
    .stMetric * { color: #0f172a !important; }
    
    /* Main Title */
    h1 { color: #1e40af !important; font-weight: 800 !important; }
    
    /* Composite info notice boxes */
    .composite-info-box { 
        background-color: #f3e8ff; 
        border-left: 4px solid #7c3aed; 
        border-radius: 6px; 
        padding: 10px 14px; 
        margin-bottom: 10px; 
        font-size: 0.85em; 
        color: #5b21b6; 
    }
    
    /* Reference info notice boxes */
    .ref-info-box { 
        background-color: #fef3c7; 
        border-left: 4px solid #d97706; 
        border-radius: 6px; 
        padding: 10px 14px; 
        margin-bottom: 10px; 
        font-size: 0.85em; 
        color: #92400e; 
    }
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
    data_sheet = next((s for s in xf.sheet_names if s.strip().upper() == "DATA"), None)

    if not gnrl_sheet or not data_sheet:
        st.error("❌ The 'GNRL' or 'DATA' sheets could not be found.")
        return None, None

    raw = pd.read_excel(buf, sheet_name=gnrl_sheet, engine="openpyxl", header=None)
    header_row = next((i for i, r in raw.iterrows() if any("sample" in str(v).lower() or "stn" in str(v).lower() for v in r if pd.notna(v))), None)
    if header_row is None: return None, None

    gnrl = pd.read_excel(buf, sheet_name=gnrl_sheet, engine="openpyxl", header=header_row)
    gnrl.columns = norm_cols(gnrl.columns)
    gnrl = gnrl.dropna(how="all")

    stn_cols = [c for c in gnrl.columns if "stn" in c or "sample" in c]
    if not stn_cols: return None, None

    short_col = next((c for c in stn_cols if gnrl[c].dropna().astype(str).str.strip().str.match(r'^E\d+').mean() > 0.4), stn_cols[-1])
    gnrl = gnrl.rename(columns={short_col: "stn"})
    gnrl["stn"] = gnrl["stn"].astype(str).str.strip().str.upper()
    gnrl = gnrl[gnrl["stn"].str.match(STN_PATTERN, na=False)]

    mass_col = next((c for c in gnrl.columns if "surface_mass" in c), None)
    if mass_col: gnrl[mass_col] = pd.to_numeric(gnrl[mass_col], errors="coerce")
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

    data["stn"] = (data["stn"].astype(str).replace({"nan": pd.NA, "None": pd.NA, "": pd.NA}).ffill().str.strip().str.upper())
    data = data[data["stn"].str.match(STN_PATTERN, na=False)]

    for col in ["frequency", "alpha_cabin", "alpha_kundt"]:
        if col in data.columns: data[col] = pd.to_numeric(data[col], errors="coerce")

    merged = data.merge(gnrl, on="stn", how="left")
    merged["is_ref"]       = merged["is_ref"].fillna(False)
    merged["is_composite"] = merged["is_composite"].fillna(False)
    merged["curve_label"]  = merged.apply(lambda r: build_curve_label(r, mass_col), axis=1)
    return merged, mass_col

# ─────────────────────────────────────────────────────────────────
# MAIN UI & DYNAMIC LOADING
# ─────────────────────────────────────────────────────────────────
st.title("🔊 DATABASE")

# --- INITIAL LOAD VIA GITHUB API ---
current_filename, excel_data = find_and_download_current_file()

# --- ADMIN PANEL : FILE RE-UPLOAD ---
with st.sidebar.expander("🔄 GitHub Administration", expanded=False):
    uploaded_file = st.file_uploader("Upload a new database file", type=["xlsx"], help="The filename must contain 'Database' followed by a version number (e.g. database_v2.xlsx, DATABASE-V3.xlsx)")
    if uploaded_file:
        file_bytes = uploaded_file.read()
        filename_uploaded = uploaded_file.name
        
        if not re.match(FILENAME_REGEX, filename_uploaded.lower()):
            st.error("⚠️ Invalid filename! The name must contain 'Database' followed by a version indicator (e.g. Database_V4.xlsx, database-v2.xlsx)")
        else:
            if st.button("🚀 Overwrite & Publish Version"):
                with st.spinner("Uploading to GitHub..."):
                    if upload_new_excel_to_github(filename_uploaded, file_bytes):
                        st.success(f"✅ Successfully deployed: {filename_uploaded}")
                        st.cache_data.clear()
                        import time
                        time.sleep(1.5)  # Let GitHub API stabilize
                        st.rerun()

# --- ERROR HANDLING FOR UNINITIALIZED REPO ---
if excel_data is None:
    st.error("❌ No valid database file (e.g. 'Database_V1.xlsx') was found on GitHub.")
    st.info("Please use the Administration module in the sidebar to initialize the database.")
    st.stop()

# Active active database info tag
st.caption(f"📂 Active database loaded from GitHub: `{current_filename}`")

df, mass_col = load_data(excel_data)
if df is None: st.stop()

# ─────────────────────────────────────────────────────────────────
# SIDEBAR FILTERS
# ─────────────────────────────────────────────────────────────────
st.sidebar.header("🎛️ Global Filters")
n_comp   = int(df[~df["is_ref"]].groupby("stn")["is_composite"].first().sum())
n_single = int((~df[~df["is_ref"]].groupby("stn")["is_composite"].first()).sum())
sample_type = st.sidebar.radio("Sample Type", ["All", "Single Layer Only", "Composite Only"], index=0, help=f"{n_comp} composite · {n_single} single-layer")
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

st.sidebar.markdown("<small><span style='color:#7c3aed'>⊕</span> composite — dashed line<br><span style='color:#d97706'>★</span> reference — bold gold line</small>", unsafe_allow_html=True)
abs_type = st.sidebar.radio("Measurement Method", ["alpha_cabin", "alpha_kundt"])

all_active_labels = selected_labels

# ─────────────────────────────────────────────────────────────────
# RANKING VS REFERENCE (Sidebar Bottom) - WEIGHTED LOW FREQS
# ─────────────────────────────────────────────────────────────────
with st.sidebar.expander("🏆 Ranking vs Reference", expanded=False):
    st.markdown("<small>Identify the best samples compared to a target curve (analyzed up to 2 kHz, <b>weighted for low frequencies</b>).</small>", unsafe_allow_html=True)
    
    if available_labels:
        target_ref = st.selectbox("Select Target", options=["-- Select --"] + available_labels)
        
        if target_ref != "-- Select --":
            if st.button("Run Analysis"):
                # 1. Isolate target reference data and limit to <= 2000 Hz
                ref_data = fdf[(fdf["curve_label"] == target_ref) & (fdf["frequency"] <= 2000)].dropna(subset=["frequency", abs_type])
                ref_data = ref_data.groupby("frequency", as_index=False)[abs_type].mean()
                ref_dict = dict(zip(ref_data["frequency"], ref_data[abs_type]))
                
                always_above = []
                ranking = []
                
                # 2. Analyze other samples
                candidates = [l for l in available_labels if l != target_ref]
                
                for cand in candidates:
                    cand_data = fdf[(fdf["curve_label"] == cand) & (fdf["frequency"] <= 2000)].dropna(subset=["frequency", abs_type])
                    if cand_data.empty: continue
                    cand_data = cand_data.groupby("frequency", as_index=False)[abs_type].mean()
                    
                    weighted_diffs = []
                    sum_weights = 0
                    is_always_above = True
                    
                    # Point-by-point comparison (by frequency)
                    for _, row in cand_data.iterrows():
                        freq = row["frequency"]
                        val = row[abs_type]
                        if freq in ref_dict:
                            diff = val - ref_dict[freq]
                            
                            # 💡 PONDÉRATION : inversement proportionnelle à la fréquence.
                            # Plus la fréquence est basse, plus le poids est fort.
                            # Exemple : freq = 400 Hz -> weight = 10 | freq = 4000 Hz -> weight = 1
                            weight = 2000.0 / freq
                            
                            weighted_diffs.append(diff * weight)
                            sum_weights += weight
                            
                            if diff < 0:
                                is_always_above = False
                    
                    # If there are common frequencies, calculate the score
                    if weighted_diffs and sum_weights > 0:
                        avg_diff = sum(weighted_diffs) / sum_weights
                        data_dict = {"Sample": cand, "Weighted Score (α)": round(avg_diff, 4)}
                        
                        if is_always_above:
                            always_above.append(data_dict)
                        ranking.append(data_dict)
                
                # 3. Display results
                if always_above:
                    st.success("✅ Samples consistently above (or equal to) the reference up to 2 kHz:")
                    df_always = pd.DataFrame(always_above).sort_values(by="Weighted Score (α)", ascending=False)
                    st.dataframe(df_always, hide_index=True, use_container_width=True)
                else:
                    st.info("No sample outperforms the reference across the entire frequency range (up to 2 kHz).")
                    
                    if ranking:
                        st.write("🔝 **Top 5 Best Alternatives (Weighted for LF):**")
                        # Sort by highest weighted average delta
                        top5 = pd.DataFrame(ranking).sort_values(by="Weighted Score (α)", ascending=False).head(5)
                        st.dataframe(top5, hide_index=True, use_container_width=True)
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

    if n_sel_comp > 0:
        comp_stns = [l.split("|")[0].strip().lstrip("⊕").strip() for l in all_active_labels if l.startswith("⊕")]
        st.markdown(f'<div class="composite-info-box"><b>🔀 Composite samples:</b> {", ".join(comp_stns)}<br><span>Two superimposed material layers — shown as <b>dashed lines</b>.</span></div>', unsafe_allow_html=True)
    if n_sel_ref > 0:
        ref_names = [l.lstrip("★").strip() for l in all_active_labels if l.startswith("★")]
        st.markdown(f'<div class="ref-info-box"><b>📌 Reference curves:</b> {", ".join(ref_names)}<br><span>Benchmark data — shown as <b>bold gold lines</b>.</span></div>', unsafe_allow_html=True)

    FREQ_TICKS = {
        "alpha_cabin": [315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000],
        "alpha_kundt": [200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300]
    }
    ticks = FREQ_TICKS.get(abs_type, sorted(plot_data["frequency"].dropna().unique()))

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
            color, line_dash, line_width, marker_sym, marker_sz, hover_tag = REF_COLOR, "solid", 3.5, "star", 10, " <i>(reference)</i>"
        elif comp_curve:
            color, line_dash, line_width, marker_sym, marker_sz, hover_tag = COLORS[color_idx % len(COLORS)], "dash", 2.8, "diamond", 7, " <i>(composite)</i>"; color_idx += 1
        else:
            color, line_dash, line_width, marker_sym, marker_sz, hover_tag = COLORS[color_idx % len(COLORS)], "solid", 2.5, "circle", 6, ""; color_idx += 1

        fig.add_trace(go.Scatter(
            x=sub["frequency"], y=sub[abs_type], mode="lines+markers", name=label,
            line=dict(color=color, width=line_width, dash=line_dash), marker=dict(color=color, size=marker_sz, symbol=marker_sym),
            hovertemplate=f"<b>%{{fullData.name}}</b><br>Freq: %{{x}} Hz<br>α: %{{y:.3f}}{hover_tag}<extra></extra>"
        ))

    fig.update_layout(
        title=f"Sound Absorption Coefficients ({abs_type.replace('_', ' ').title()})",
        xaxis=dict(title="Frequency (Hz)", type="log", tickmode="array", tickvals=ticks, ticktext=[str(t) for t in ticks], showgrid=True, gridcolor="#e2e8f0"),
        yaxis=dict(title="Absorption Coefficient α", range=[-0.05, 1.1], showgrid=True, gridcolor="#e2e8f0"),
        hovermode="x unified", plot_bgcolor="#ffffff", paper_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", yanchor="bottom", y=-0.35, xanchor="center", x=0.5), height=640
    )

    st.plotly_chart(fig, width="stretch")

    html_bytes = pio.to_html(fig, include_plotlyjs="cdn", full_html=True).encode("utf-8")
    st.download_button(
        label="📥 Download chart as HTML",
        data=html_bytes,
        file_name="absorption_curves.html",
        mime="text/html",
    )
with tab2:
    st.markdown("### 📥 Download Global Source File")
    st.download_button(
        label=f"🟢 Download complete file currently online ({current_filename})",
        data=excel_data,
        file_name=current_filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    st.markdown("---")

    st.markdown("### Screened Dataset")
    extra_cols = [c for c in ["is_ref", "is_composite"] if c in plot_data.columns]
    show_cols  = [c for c in ["stn", "curve_label"] + extra_cols + ["frequency", abs_type] if c in plot_data.columns]
    grp_cols   = ["stn", "curve_label"] + extra_cols + ["frequency"]
    raw_output = plot_data[show_cols].groupby(grp_cols, as_index=False)[abs_type].mean().sort_values(["stn", "frequency"])
    raw_output = raw_output.rename(columns={"is_composite": "Composite", "is_ref": "Reference"})

    st.dataframe(raw_output, width="stretch", hide_index=True)
    csv = raw_output.to_csv(index=False).encode("utf-8")
    st.download_button("📥 Export Current Table (.CSV)", csv, "filtered_data.csv", "text/csv")
