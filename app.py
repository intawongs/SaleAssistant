import streamlit as st
import pandas as pd
import time
import datetime
import speech_recognition as sr
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from streamlit_mic_recorder import mic_recorder
import io
from pydub import AudioSegment # <--- เพิ่มตัวแปลงไฟล์

st.set_page_config(page_title="RC Sales AI (Cloud Voice Fix)", layout="wide", page_icon="☁️")

# ==========================================
# 1. GOOGLE SHEETS CONNECTION
# ==========================================
SHEET_NAME = "RC_Sales_Database"

@st.cache_resource
def init_connection():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(st.secrets["gcp_service_account"], scope)
    client = gspread.authorize(creds)
    return client

@st.cache_data(ttl=60)
def get_data(worksheet_name):
    try:
        client = init_connection()
        sheet = client.open(SHEET_NAME)
        worksheet = sheet.worksheet(worksheet_name)
        data = worksheet.get_all_records()
        df = pd.DataFrame(data)
        if not df.empty:
            df.columns = [str(c).strip() for c in df.columns]
        return df
    except:
        return pd.DataFrame()

def append_data(worksheet_name, row_data):
    try:
        client = init_connection()
        sheet = client.open(SHEET_NAME)
        worksheet = sheet.worksheet(worksheet_name)
        worksheet.append_row(row_data)
        st.cache_data.clear()
    except Exception as e:
        st.error(f"Error saving data: {e}")

def delete_mission_from_sheet(customer_name):
    try:
        client = init_connection()
        sheet = client.open(SHEET_NAME)
        ws = sheet.worksheet("Missions")
        data = ws.get_all_records()
        rows_to_delete = []
        for i, row in enumerate(data):
            if row.get('Customer') == customer_name:
                rows_to_delete.append(i + 2) 
        for r in reversed(rows_to_delete):
            ws.delete_rows(r)
        st.cache_data.clear()
    except Exception as e:
        st.error(f"Error deleting mission: {e}")

# ==========================================
# 2. VOICE FUNCTION (FIXED: WebM -> WAV)
# ==========================================
def transcribe_audio(audio_bytes):
    r = sr.Recognizer()
    
    try:
        # 1. แปลง WebM (จาก Browser) เป็น WAV (ที่ SpeechRecognition อ่านออก)
        audio_segment = AudioSegment.from_file(io.BytesIO(audio_bytes))
        wav_io = io.BytesIO()
        audio_segment.export(wav_io, format="wav")
        wav_io.seek(0) # รีเซ็ตเข็มอ่านไฟล์ไปที่จุดเริ่มต้น

        # 2. ส่ง WAV ไปถอดความ
        with sr.AudioFile(wav_io) as source:
            audio_data = r.record(source)
            text = r.recognize_google(audio_data, language="th-TH")
            return text
            
    except Exception as e:
        # st.error(f"Debug Error: {e}") # เปิดบรรทัดนี้ถ้าอยากเห็น error เต็มๆ
        return None

# ==========================================
# 3. LOAD DATA
# ==========================================
try:
    df_assignments = get_data("Assignments")
    df_missions = get_data("Missions")
except:
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
    st.header("👮 Manager Dashboard")
    
    tab1, tab2, tab3 = st.tabs(["📝 สั่งงาน", "📂 ดูข้อมูลดิบ", "📊 รายงานผล"])
    
    with tab1:
        st.subheader("มอบหมายงาน")
        col1, col2 = st.columns(2)
        with col1:
            sales_list = df_assignments['Sales_Rep'].unique() if not df_assignments.empty else []
            selected_sale = st.selectbox("Sales Rep", sales_list)
            cust_list = []
            if not df_assignments.empty and selected_sale:
                cust_list = df_assignments[df_assignments['Sales_Rep'] == selected_sale]['Customer'].unique()
            selected_cust = st.selectbox("Customer", cust_list)
        
        with col2:
            topic = st.text_input("หัวข้องาน")
            desc = st.text_input("รายละเอียด")
            if st.button("➕ บันทึก (Save to Cloud)", type="primary"):
                if topic and selected_cust:
                    row = [selected_cust, topic, desc, "pending"]
                    append_data("Missions", row)
                    st.success(f"สั่งงานไปที่ {selected_cust} แล้ว!")
                    time.sleep(1)
                    st.rerun()

    with tab2:
        st.dataframe(df_missions)
    with tab3:
        try:
            df_reports = get_data("Reports")
            st.dataframe(df_reports)
        except:
            st.info("ยังไม่มีรายงานเข้ามา")

# --- SALES ROLE ---
else:
    st.header("📱 Sales App")
    
    sales_list = df_assignments['Sales_Rep'].unique() if not df_assignments.empty else []
    current_user = st.selectbox("👤 Login:", sales_list)
    
    my_custs = []
    if not df_assignments.empty and current_user:
        my_custs = df_assignments[df_assignments['Sales_Rep'] == current_user]['Customer'].unique()
    
    st.divider()
    target_cust = st.selectbox("🏢 เลือกลูกค้าที่เข้าเยี่ยม:", my_custs)
    
    my_missions = pd.DataFrame()
    if not df_missions.empty and 'Customer' in df_missions.columns:
        my_missions = df_missions[df_missions['Customer'] == target_cust]
    
    if my_missions.empty:
        st.success("🎉 ไม่มีงานค้าง (All Clear)")
    else:
        st.subheader(f"📋 งานที่ต้องทำ: {target_cust}")
        
        checklist_status = st.session_state.sales_checklist.get(target_cust, set())
        completed_count = 0
        
        for index, row in my_missions.iterrows():
            topic = row['topic']
            is_done = topic in checklist_status
            icon = "✅" if is_done else "❌"
            st.write(f"{icon} **{topic}**: {row['desc']}")
            if is_done: completed_count += 1
            
        st.divider()
        
        # --- VOICE RECORDER UI ---
        if completed_count < len(my_missions):
            col_rec, col_info = st.columns([1, 3])
            
            with col_rec:
                st.write("🎙️ **กดปุ่มเพื่อพูด:**")
                audio = mic_recorder(
                    start_prompt="เริ่มพูด",
                    stop_prompt="หยุดพูด (ส่ง)",
                    just_once=True,
                    use_container_width=True,
                    format="webm" # ระบุชัดเจนว่ารับ webm
                )
            
            with col_info:
                if audio:
                    with st.spinner("กำลังแปลงเสียงเป็นข้อความ..."):
                        text = transcribe_audio(audio['bytes'])
                        if text:
                            st.success(f"🗣️ ได้ยินว่า: **{text}**")
                            if completed_count == 0:
                                 checklist_status.add(my_missions.iloc[0]['topic'])
                            else:
                                 for _, r in my_missions.iterrows(): checklist_status.add(r['topic'])
                            st.session_state.sales_checklist[target_cust] = checklist_status
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.warning("ฟังไม่ออกครับ ลองพูดใหม่")
                else:
                    st.info("รอรับเสียง...")
                
                st.warning(f"เหลืออีก {len(my_missions) - completed_count} ข้อ")

        else:
            st.success("✅ ครบถ้วน!")
            if st.button("🚀 ปิดงาน (Save to Cloud)", type="primary"):
                topics_str = ", ".join(my_missions['topic'].tolist())
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                report_row = [timestamp, current_user, target_cust, topics_str, "Completed"]
                
                append_data("Reports", report_row)
                delete_mission_from_sheet(target_cust)
                
                if target_cust in st.session_state.sales_checklist:
                    del st.session_state.sales_checklist[target_cust]
                
                st.toast("บันทึกเรียบร้อย!", icon="☁️")
                time.sleep(2)
                st.rerun()