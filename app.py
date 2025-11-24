import streamlit as st
import pandas as pd
import time
import datetime
import speech_recognition as sr
import gspread
from oauth2client.service_account import ServiceAccountCredentials

st.set_page_config(page_title="RC Sales AI (Google Sheets)", layout="wide", page_icon="☁️")

# ==========================================
# 1. GOOGLE SHEETS CONNECTION
# ==========================================
# ชื่อไฟล์ Google Sheet ที่คุณตั้งไว้ (ต้องตรงเป๊ะๆ)
SHEET_NAME = "RC_Sales_Database"

@st.cache_resource
def init_connection():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    # ดึง Secret จาก Streamlit Cloud หรือไฟล์ secrets.toml
    creds = ServiceAccountCredentials.from_json_keyfile_dict(st.secrets["gcp_service_account"], scope)
    client = gspread.authorize(creds)
    return client

def get_data(worksheet_name):
    client = init_connection()
    sheet = client.open(SHEET_NAME)
    worksheet = sheet.worksheet(worksheet_name)
    return pd.DataFrame(worksheet.get_all_records())

def append_data(worksheet_name, row_data):
    """เพิ่มแถวใหม่ (สำหรับ Report และ Mission ใหม่)"""
    client = init_connection()
    sheet = client.open(SHEET_NAME)
    worksheet = sheet.worksheet(worksheet_name)
    worksheet.append_row(row_data)

def delete_mission_from_sheet(customer_name):
    """ลบ Mission ของลูกค้าที่ทำเสร็จแล้ว (Advance Logic)"""
    client = init_connection()
    sheet = client.open(SHEET_NAME)
    ws = sheet.worksheet("Missions")
    
    # อ่านข้อมูลทั้งหมดมาก่อน
    data = ws.get_all_records()
    
    # หาว่าแถวไหนต้องลบ (เก็บ Index ไว้)
    # หมายเหตุ: gspread แถวเริ่มที่ 1 และมี header เป็น 1 ดังนั้น data index 0 คือ row 2
    rows_to_delete = []
    for i, row in enumerate(data):
        if row['customer'] == customer_name:
            rows_to_delete.append(i + 2) # +2 เพราะ index เริ่ม 0 และ header
    
    # ลบจากล่างขึ้นบน เพื่อไม่ให้ index เพี้ยน
    for r in reversed(rows_to_delete):
        ws.delete_rows(r)

# ==========================================
# 2. VOICE FUNCTION
# ==========================================
def record_voice():
    r = sr.Recognizer()
    with sr.Microphone() as source:
        r.adjust_for_ambient_noise(source, duration=0.5)
        st.toast("กำลังฟัง... 🎙️", icon="👂")
        try:
            audio = r.listen(source, timeout=5, phrase_time_limit=15)
            text = r.recognize_google(audio, language="th-TH")
            return text
        except:
            return None

# ==========================================
# 3. INIT & LOAD DATA
# ==========================================
# โหลดข้อมูลสดๆ จาก Google Sheets ทุกครั้งที่รีเฟรช
try:
    df_assignments = get_data("Assignments")
    df_missions = get_data("Missions")
except Exception as e:
    st.error(f"เชื่อมต่อ Google Sheets ไม่ได้: {e}")
    st.stop()

if 'sales_checklist' not in st.session_state:
    st.session_state.sales_checklist = {}

# ==========================================
# 4. UI & LOGIC
# ==========================================
user_role = st.sidebar.radio("Login Role:", ("Sales Manager", "Sales Rep"))

if st.sidebar.button("🔄 Refresh Data"):
    st.cache_data.clear()
    st.rerun()

# --- MANAGER ROLE ---
if user_role == "Sales Manager":
    st.header("👮 Manager Dashboard (Connected to GSheets)")
    
    tab1, tab2, tab3 = st.tabs(["📝 สั่งงาน", "📂 ดูข้อมูลดิบ", "📊 รายงานผล"])
    
    with tab1:
        st.subheader("มอบหมายงาน")
        col1, col2 = st.columns(2)
        with col1:
            sales_list = df_assignments['Sales_Rep'].unique() if not df_assignments.empty else []
            selected_sale = st.selectbox("Sales Rep", sales_list)
            
            cust_list = df_assignments[df_assignments['Sales_Rep'] == selected_sale]['Customer'].unique() if not df_assignments.empty else []
            selected_cust = st.selectbox("Customer", cust_list)
        
        with col2:
            topic = st.text_input("หัวข้อ")
            desc = st.text_input("รายละเอียด")
            
            if st.button("➕ บันทึก (Save to Cloud)", type="primary"):
                if topic and selected_cust:
                    # บันทึกลง Google Sheets
                    row = [selected_cust, topic, desc, "pending"]
                    append_data("Missions", row)
                    st.success(f"สั่งงานไปที่ {selected_cust} แล้ว!")
                    time.sleep(1)
                    st.rerun()

    with tab2:
        st.write("### Active Missions (บน Cloud)")
        st.dataframe(df_missions)
        st.write("### Assignments Map")
        st.dataframe(df_assignments)

    with tab3:
        st.write("### Completed Reports")
        try:
            df_reports = get_data("Reports")
            st.dataframe(df_reports)
        except:
            st.info("ยังไม่มีรายงาน")

# --- SALES ROLE ---
else:
    st.header("📱 Sales App (Online Mode)")
    
    sales_list = df_assignments['Sales_Rep'].unique() if not df_assignments.empty else []
    current_user = st.selectbox("👤 Login:", sales_list)
    
    my_custs = df_assignments[df_assignments['Sales_Rep'] == current_user]['Customer'].unique() if not df_assignments.empty else []
    st.divider()
    
    target_cust = st.selectbox("🏢 เลือกลูกค้า:", my_custs)
    
    # Filter Missions จาก DataFrame ที่โหลดมา
    my_missions = df_missions[df_missions['customer'] == target_cust]
    
    if my_missions.empty:
        st.success("🎉 ไม่มีงานค้าง! (All Clear)")
    else:
        st.subheader(f"📋 Mission: {target_cust}")
        
        checklist_status = st.session_state.sales_checklist.get(target_cust, set())
        completed_count = 0
        
        # แสดงรายการ
        for index, row in my_missions.iterrows():
            topic = row['topic']
            is_done = topic in checklist_status
            icon = "✅" if is_done else "❌"
            st.write(f"{icon} **{topic}**: {row['desc']}")
            if is_done: completed_count += 1
            
        st.divider()
        st.info("กดปุ่มรายงานผล (ด้วยเสียง)")
        
        # Logic ปุ่มรายงาน
        if completed_count < len(my_missions):
            col_btn, col_txt = st.columns([1, 3])
            with col_btn:
                if st.button("🎙️ พูดรายงาน"):
                    text = record_voice()
                    if text:
                        st.session_state['last_voice'] = text
                        # Auto-tick checklist for demo flow
                        if completed_count == 0:
                             checklist_status.add(my_missions.iloc[0]['topic'])
                        else:
                             for _, r in my_missions.iterrows(): checklist_status.add(r['topic'])
                        st.session_state.sales_checklist[target_cust] = checklist_status
                        st.rerun()
            
            with col_txt:
                st.write(f"🗣️: {st.session_state.get('last_voice', '...')}")
                st.warning(f"เหลืออีก {len(my_missions) - completed_count} ข้อ")
        
        else:
            st.success("✅ ครบถ้วน!")
            if st.button("🚀 ปิดงาน (Save to Cloud)", type="primary"):
                # 1. Save Report to Google Sheets
                topics_str = ", ".join(my_missions['topic'].tolist())
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                report_row = [timestamp, current_user, target_cust, topics_str, "Completed"]
                
                append_data("Reports", report_row)
                
                # 2. Delete Missions from Google Sheets
                delete_mission_from_sheet(target_cust)
                
                # 3. Clear local state
                if target_cust in st.session_state.sales_checklist:
                    del st.session_state.sales_checklist[target_cust]
                
                st.toast("บันทึกขึ้น Cloud เรียบร้อย!", icon="☁️")
                time.sleep(2)
                st.rerun()