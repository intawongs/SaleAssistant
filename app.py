import streamlit as st
import pandas as pd
import time
import datetime
import speech_recognition as sr
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ตั้งค่าหน้าจอ
st.set_page_config(page_title="RC Sales AI (Smart Cache)", layout="wide", page_icon="🚀")

# ชื่อไฟล์ Google Sheet
SHEET_NAME = "RC_Sales_Database"

# ==========================================
# 1. GOOGLE SHEETS CONNECTION & CACHING
# ==========================================

@st.cache_resource
def init_connection():
    """สร้างการเชื่อมต่อ (ทำครั้งเดียวตลอดการรันแอป)"""
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    # ดึง Secrets จาก Streamlit Cloud
    creds = ServiceAccountCredentials.from_json_keyfile_dict(st.secrets["gcp_service_account"], scope)
    client = gspread.authorize(creds)
    return client

@st.cache_data(ttl=60)  # <--- KEY FIX: จำข้อมูลไว้ 60 วินาที ลดการเรียก API
def get_data(worksheet_name):
    """ดึงข้อมูลจาก Sheet มาแสดง"""
    try:
        client = init_connection()
        sheet = client.open(SHEET_NAME)
        worksheet = sheet.worksheet(worksheet_name)
        data = worksheet.get_all_records()
        df = pd.DataFrame(data)
        
        # ลบช่องว่างหัวตาราง (ป้องกัน Error พิมพ์ผิด)
        if not df.empty:
            df.columns = [str(c).strip() for c in df.columns]
        return df
    except Exception as e:
        st.error(f"Error reading {worksheet_name}: {e}")
        return pd.DataFrame()

def append_data(worksheet_name, row_data):
    """เพิ่มข้อมูลลง Sheet"""
    try:
        client = init_connection()
        sheet = client.open(SHEET_NAME)
        worksheet = sheet.worksheet(worksheet_name)
        worksheet.append_row(row_data)
        
        # <--- KEY FIX: ล้างความจำทันที เพื่อให้เห็นข้อมูลใหม่
        st.cache_data.clear()
    except Exception as e:
        st.error(f"Error saving data: {e}")

def delete_mission_from_sheet(customer_name):
    """ลบ Mission ที่เสร็จแล้ว"""
    try:
        client = init_connection()
        sheet = client.open(SHEET_NAME)
        ws = sheet.worksheet("Missions")
        
        data = ws.get_all_records()
        
        # หาแถวที่จะลบ (gspread row เริ่มที่ 1, header คือ 1, data เริ่ม 2)
        rows_to_delete = []
        for i, row in enumerate(data):
            # ใช้ .get('Customer') เพื่อความชัวร์
            if row.get('Customer') == customer_name:
                rows_to_delete.append(i + 2) 
        
        # ลบย้อนกลับ (ล่างขึ้นบน) เพื่อไม่ให้ Index เพี้ยน
        for r in reversed(rows_to_delete):
            ws.delete_rows(r)
        
        # <--- KEY FIX: ล้างความจำทันที
        st.cache_data.clear()
    except Exception as e:
        st.error(f"Error deleting mission: {e}")

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
        except sr.WaitTimeoutError:
            st.warning("ไม่ได้ยินเสียงครับ")
            return None
        except sr.UnknownValueError:
            st.warning("ฟังไม่ออกครับ")
            return None
        except:
            return None

# ==========================================
# 3. LOAD DATA (With Error Handling)
# ==========================================
try:
    df_assignments = get_data("Assignments")
    df_missions = get_data("Missions")
except:
    st.warning("กำลังเชื่อมต่อ Google Sheets... (ถ้าค้างนานให้กด Refresh)")
    st.stop()

if 'sales_checklist' not in st.session_state:
    st.session_state.sales_checklist = {}

# ==========================================
# 4. UI & LOGIC
# ==========================================
user_role = st.sidebar.radio("Login Role:", ("Sales Manager", "Sales Rep"))

if st.sidebar.button("🔄 Refresh Data (Force Update)"):
    st.cache_data.clear()
    st.rerun()

# ------------------------------------------
# ROLE: SALES MANAGER
# ------------------------------------------
if user_role == "Sales Manager":
    st.header("👮 Manager Dashboard")
    
    # เช็คว่าข้อมูลโหลดมาจริงไหม
    if df_assignments.empty:
        st.error("ไม่พบข้อมูลในแท็บ Assignments กรุณาตรวจสอบ Google Sheet")
    
    tab1, tab2, tab3 = st.tabs(["📝 สั่งงาน", "📂 ดูข้อมูลดิบ", "📊 รายงานผล"])
    
    with tab1:
        st.subheader("มอบหมายงาน")
        col1, col2 = st.columns(2)
        with col1:
            # Dropdown: Sales Rep
            sales_list = df_assignments['Sales_Rep'].unique() if not df_assignments.empty else []
            selected_sale = st.selectbox("Sales Rep", sales_list)
            
            # Dropdown: Customer (กรองตาม Sale)
            cust_list = []
            if not df_assignments.empty and selected_sale:
                cust_list = df_assignments[df_assignments['Sales_Rep'] == selected_sale]['Customer'].unique()
            selected_cust = st.selectbox("Customer", cust_list)
        
        with col2:
            topic = st.text_input("หัวข้องาน")
            desc = st.text_input("รายละเอียด")
            
            if st.button("➕ บันทึก (Save to Cloud)", type="primary"):
                if topic and selected_cust:
                    # บันทึกลง Sheet Missions
                    # Row Format: [Customer, topic, desc, status]
                    row = [selected_cust, topic, desc, "pending"]
                    append_data("Missions", row)
                    st.success(f"สั่งงานไปที่ {selected_cust} แล้ว!")
                    time.sleep(1)
                    st.rerun()

    with tab2:
        st.write("### Active Missions (Missions Tab)")
        st.dataframe(df_missions)
        st.write("### Assignments Map (Assignments Tab)")
        st.dataframe(df_assignments)

    with tab3:
        st.write("### Completed Reports (Reports Tab)")
        try:
            df_reports = get_data("Reports")
            st.dataframe(df_reports)
        except:
            st.info("ยังไม่มีรายงานเข้ามา")

# ------------------------------------------
# ROLE: SALES REP
# ------------------------------------------
else:
    st.header("📱 Sales App (Voice Enabled)")
    
    # 1. Login
    sales_list = df_assignments['Sales_Rep'].unique() if not df_assignments.empty else []
    current_user = st.selectbox("👤 ระบุชื่อของคุณ (Login):", sales_list)
    
    # 2. Select Customer
    my_custs = []
    if not df_assignments.empty and current_user:
        my_custs = df_assignments[df_assignments['Sales_Rep'] == current_user]['Customer'].unique()
        
    st.divider()
    target_cust = st.selectbox("🏢 เลือกลูกค้าที่เข้าเยี่ยม:", my_custs)
    
    # 3. Filter Missions
    # ป้องกัน Error กรณี df_missions ว่าง หรือไม่มีคอลัมน์ Customer
    my_missions = pd.DataFrame()
    if not df_missions.empty and 'Customer' in df_missions.columns:
        my_missions = df_missions[df_missions['Customer'] == target_cust]
    
    # --- Display Missions ---
    if my_missions.empty:
        st.success("🎉 ไม่มีงานค้างสำหรับลูกค้ารายนี้ (All Clear)")
    else:
        st.subheader(f"📋 งานที่ต้องทำ: {target_cust}")
        
        checklist_status = st.session_state.sales_checklist.get(target_cust, set())
        completed_count = 0
        
        # Loop แสดง Checklist
        for index, row in my_missions.iterrows():
            topic = row['topic']
            is_done = topic in checklist_status
            icon = "✅" if is_done else "❌"
            st.write(f"{icon} **{topic}**: {row['desc']}")
            if is_done: completed_count += 1
            
        st.divider()
        st.info("🎙️ กดปุ่มเพื่อพูดรายงานผล")
        
        # --- Logic ปุ่มรายงาน ---
        if completed_count < len(my_missions):
            
            col_btn, col_txt = st.columns([1, 3])
            with col_btn:
                # ปุ่มพูด
                if st.button("🎙️ พูดรายงาน"):
                    text = record_voice()
                    if text:
                        st.session_state['last_voice'] = text
                        
                        # Logic การติ๊กถูก: รอบแรกติ๊ก 1, รอบสองติ๊กหมด
                        if completed_count == 0:
                             checklist_status.add(my_missions.iloc[0]['topic'])
                        else:
                             for _, r in my_missions.iterrows(): checklist_status.add(r['topic'])
                        
                        st.session_state.sales_checklist[target_cust] = checklist_status
                        st.rerun()
            
            with col_txt:
                st.caption("ข้อความเสียงล่าสุด:")
                st.write(f"🗣️ \"{st.session_state.get('last_voice', '...')}\"")
                st.warning(f"เหลืออีก {len(my_missions) - completed_count} ข้อ (ต้องครบถึงจะปิดงานได้)")
        
        else:
            # ครบแล้ว -> ปุ่มปิดงานโผล่
            st.success("✅ ข้อมูลครบถ้วน!")
            if st.button("🚀 ปิดงาน (Save to Cloud)", type="primary"):
                
                # 1. เตรียมข้อมูลลง Sheet Reports
                topics_str = ", ".join(my_missions['topic'].tolist())
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                # Row Format: [Timestamp, Sales_Rep, Customer, Topics_Covered, Status]
                report_row = [timestamp, current_user, target_cust, topics_str, "Completed"]
                
                append_data("Reports", report_row)
                
                # 2. ลบ Mission ออกจาก Sheet Missions
                delete_mission_from_sheet(target_cust)
                
                # 3. ล้าง Checklist ในเครื่อง
                if target_cust in st.session_state.sales_checklist:
                    del st.session_state.sales_checklist[target_cust]
                
                st.toast("บันทึกเรียบร้อย! ข้อมูลขึ้น Cloud แล้ว", icon="☁️")
                time.sleep(2)
                st.rerun()