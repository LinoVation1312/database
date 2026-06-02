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

if "GITHUB_TOKEN" in st.secrets:
    TOKEN = st.secrets["GITHUB_TOKEN"]
else:
    TOKEN = None

headers = {"Authorization": f"token {TOKEN}", "Accept": "application/vnd.github.v3+json"} if TOKEN else {}
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
        st.error("❌ GITHUB_TOKEN is missing from secrets.")
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
        put_data = {"message": f"Update: {new_filename}", "content": content_b64, "branch": BRANCH}
        if existing_sha: put_data["sha"] = existing_sha
        put_res = requests.put(upload_url, headers=headers, json=put_data)
        if put_res.status_code not in [200, 201]: return False
        if old_filename_to_delete:
            delete_url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{old_filename_to_delete}"
            requests.delete(delete_url, headers=headers, json={"message": "Cleanup", "sha": old_file_sha, "branch": BRANCH})
        return True
    except: return False

# ─────────────────────────────────────────────────────────────────
# PAGE CONFIG & STYLES
# ─────────────────────────────────────────────────────────────────
st.set_page_config(page_title="ACOUSTIC DATABASE", page_icon="🔊", layout="wide")

st.markdown("""
<style>
    [data-testid="stSidebar"] { background-color: #f1f5f9; border-right: 1px solid #cbd5e1; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 12px; border: 1px solid #e2e8f0; }
    h1 { color: #1e3a8a !important; font-weight: 700 !important; }
    .info-box { padding: 10px; border-radius: 8px; margin-bottom: 10px; font-size: 0.9em; }
    .composite-box { background-color: #f5f3ff; color: #5b21b6; border-left: 5px solid #8b5cf6; }
    .ref-box { background-color: #fffbeb; color: #92400e; border-left: 5px solid #f59e0b; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────
# PARSING LOGIC
# ─────────────────────────────────────────────────────────────────
MATERIAL_MAP = [(r'\bGlass\s*Fiber\b', 'GF'), (r'\bPANox\b', 'PANox'), (r'\bPES\b', 'PES'), (r'\bPET\b', 'PET'), (r'\bPP\b', 'PP')]

def norm_cols(cols):
    return (cols.str.strip().str.lower().str.replace(r'[^\w\s]', '', regex=True).str.replace(r'\s+', '_', regex=True))

def parse_materials(description: str) -> str:
    if not isinstance(description, str): return "?"
    hits = sorted([(m.start(), label) for pattern, label in MATERIAL_MAP for m in re.finditer(pattern, description, re.IGNORECASE)])
    seen, found = set(), []
    for _, label in hits:
        if label not in seen: seen.add(label); found.append(label)
    return "+".join(found) if found else "?"

def build_curve_label(row: pd.Series, mass_col: str) -> str:
    stn = str(row.get("stn", "?")).strip()
    desc = str(row.get("detailed_description", ""))
    mass = row.get(mass_col)
    thick = row.get("thickness_mm")
    if str(stn).upper().startswith("REF "): return f"★ {stn}"
    mat = parse_materials(desc)
    mass_str = f"{int(float(mass))}gsm" if pd.notna(mass) else "?"
    thick_str = f"{thick}mm" if pd.notna(thick) else "?"
    return f"{stn} | {mat} | {mass_str} | {thick_str}"

def load_data(file_bytes: bytes):
    buf = io.BytesIO(file_bytes)
    xf = pd.ExcelFile(buf, engine="openpyxl")
    gnrl_sheet = next((s for s in xf.sheet_names if s.strip().upper().startswith("GNRL")), None)
    data_sheet = next((s for s in xf.sheet_names if s.strip().upper() == "DATA"), None)
    if not gnrl_sheet or not data_sheet: return None, None
    gnrl = pd.read_excel(buf, sheet_name=gnrl_sheet, engine="openpyxl", header=7) # Header auto-detect normally
    gnrl.columns = norm_cols(gnrl.columns)
    gnrl["stn"] = gnrl.get("stn", gnrl.iloc[:,0]).astype(str).str.strip().str.upper()
    mass_col = next((c for c in gnrl.columns if "surface_mass" in c), "surface_mass")
    data = pd.read_excel(buf, sheet_name=data_sheet, engine="openpyxl")
    data.columns = norm_cols(data.columns)
    for c in data.columns:
        if "alpha_cabin" in c: data = data.rename(columns={c: "alpha_cabin"})
        elif "alpha_kundt" in c: data = data.rename(columns={c: "alpha_kundt"})
        elif "frequency" in c: data = data.rename(columns={c: "frequency"})
    data["stn"] = data["stn"].ffill().astype(str).str.strip().str.upper()
    merged = data.merge(gnrl, on="stn", how="left")
    merged["is_ref"] = merged["stn"].str.startswith("REF ")
    merged["curve_label"] = merged.apply(lambda r: build_curve_label(r, mass_col), axis=1)
    return merged, mass_col

# ─────────────────────────────────────────────────────────────────
# UI FLOW
# ─────────────────────────────────────────────────────────────────
st.title("🔊 ACOUSTIC DATABASE V2.0")

current_filename, excel_data = find_and_download_current_file()
if excel_data is None:
    st.error("No database found on GitHub.")
    st.stop()

df, mass_col = load_data(excel_data)

# Sidebar Filters
st.sidebar.header("🎛️ Settings & Filters")
abs_type = st.sidebar.radio("Measurement Type", ["alpha_cabin", "alpha_kundt"])
selected_labels = st.sidebar.multiselect("Select Samples", sorted(df["curve_label"].unique()))

# ─────────────────────────────────────────────────────────────────
# TABS
# ─────────────────────────────────────────────────────────────────
tab1, tab2 = st.tabs(["📈 Interactive Plot", "📥 Data Export"])

with tab1:
    # --- Customization Panel ---
    with st.expander("🎨 Graph Customization", expanded=True):
        col1, col2, col3 = st.columns(3)
        main_title = col1.text_input("Main Title", f"Acoustic Absorption: {abs_type.title()}")
        x_title = col2.text_input("X-Axis Title", "Frequency (Hz)")
        y_title = col3.text_input("Y-Axis Title", "Alpha Coefficient")
        
        c4, c5, c6 = st.columns(3)
        line_w = c4.slider("Global Line Width", 1.0, 6.0, 2.5)
        tick_mode = c5.selectbox("Tick Density", ["Standard", "Detailed"])
        marker_size = c6.slider("Marker Size", 0, 15, 7)
        
        c7, c8, c9 = st.columns(3)
        m_std = c7.selectbox("Standard Marker", ["circle", "square", "triangle-up"])
        m_ref = c8.selectbox("Reference Marker", ["star", "x", "cross"])
        m_comp = c9.selectbox("Composite Marker", ["diamond", "hexagon", "pentagon"])

    # Plot Logic
    fig = go.Figure()
    colors = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2", "#be185d"]
    
    plot_df = df[df["curve_label"].isin(selected_labels)]
    for i, label in enumerate(selected_labels):
        sub = plot_df[plot_df["curve_label"] == label].sort_values("frequency")
        is_ref = "REF" in label
        is_comp = "⊕" in label or "COMP" in label # Heuristic
        
        # Styles per User Request
        line_color = "red" if is_ref else colors[i % len(colors)]
        line_dash = "dot" if is_ref else ("dashdot" if is_comp else "solid")
        symbol = m_ref if is_ref else (m_comp if is_comp else m_std)
        
        fig.add_trace(go.Scatter(
            x=sub["frequency"], y=sub[abs_type], name=label,
            mode="lines+markers" if marker_size > 0 else "lines",
            line=dict(color=line_color, width=line_w + (1 if is_ref else 0), dash=line_dash),
            marker=dict(size=marker_size, symbol=symbol)
        ))

    ticks = sorted(df["frequency"].unique()) if tick_mode == "Detailed" else [315, 500, 1000, 2000, 4000, 8000]
    fig.update_layout(
        title=main_title, xaxis=dict(title=x_title, type="log", tickvals=ticks, ticktext=[str(t) for t in ticks]),
        yaxis=dict(title=y_title, range=[0, 1.1]), template="plotly_white", height=600
    )
    st.plotly_chart(fig, use_container_width=True, config={'editable': True})

with tab2:
    st.subheader("📊 Excel Export (Pivoted Table)")
    
    # 1. Pivot Data for Excel
    export_df = df[df["curve_label"].isin(selected_labels)]
    pivot_export = export_df.pivot_table(index="frequency", columns="curve_label", values=abs_type).reset_index()
    pivot_export = pivot_export.rename(columns={"frequency": "Frequency (Hz)"})
    
    # 2. UI Table
    st.write("Ready to copy or download:")
    st.dataframe(pivot_export, use_container_width=True, hide_index=True)
    
    # 3. Copy to Clipboard (Using st.code as it allows easy select-and-copy for Excel)
    st.info("💡 To copy to Excel: Click the copy icon on the right of the block below.")
    # Create tab-separated string for Excel
    tsv_data = pivot_export.to_csv(sep='\t', index=False)
    st.code(tsv_data, language="text")

    # 4. Native Excel Download with Formatting
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        pivot_export.to_excel(writer, index=False, sheet_name='Absorption_Data')
        # Auto-adjust column widths
        ws = writer.sheets['Absorption_Data']
        for col in ws.columns:
            max_len = max([len(str(cell.value)) for cell in col])
            ws.column_dimensions[col[0].column_letter].width = max_len + 5
            
    st.download_button(
        label="📥 Download as Excel (.xlsx)",
        data=output.getvalue(),
        file_name="acoustic_export.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
