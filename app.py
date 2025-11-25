import streamlit as st
import pandas as pd
import time
import datetime
import speech_recognition as sr
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from streamlit_mic_recorder import mic_recorder
import io
from pydub import AudioSegment
from groq import Groq

st.set_page_config(page_title="RC Sales AI (Final)", layout="wide", page_icon="🚀")

# ==========================================
# 1. GOOGLE SHEETS CONNECTION & CACHING
# ==========================================
SHEET_NAME = "RC_Sales_Database"

@st.cache_resource
def init_connection():
    """เชื่อมต่อ Google Sheets"""
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(st.secrets["gcp_service_account"], scope)
    client = gspread.authorize(creds)
    return client

@st.cache_data(ttl=60)
def get_data(worksheet_name):
    """อ่านข้อมูล (Cache 60 วินาที เพื่อป้องกัน Quota Exceeded)"""
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
    """บันทึกข้อมูลและล้าง Cache ทันที"""
    try:
        client = init_connection()
        sheet = client.open(SHEET_NAME)
        worksheet = sheet.worksheet(worksheet_name)
        worksheet.append_row(row_data)
        st.cache_data.clear()
    except Exception as e:
        st.error(f"Error saving data: {e}")

def delete_mission_from_sheet(customer_name):
    """ลบงานที่เสร็จแล้ว"""
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
# 2. VOICE TRANSCRIPTION (WebM -> WAV -> Text)
# ==========================================
def transcribe_audio(audio_bytes):
    r = sr.Recognizer()
    try:
        audio_segment = AudioSegment.from_file(io.BytesIO(audio_bytes))
        wav_io = io.BytesIO()
        audio_segment.export(wav_io, format="wav")
        wav_io.seek(0)
        with sr.AudioFile(wav_io) as source:
            audio_data = r.record(source)
            text = r.recognize_google(audio_data, language="th-TH")
            return text
    except Exception as e:
        return None

# ==========================================
# 3. AI LOGIC (Groq / Llama 3)
# ==========================================

# 3.1 ฟังก์ชันช่วยคิดบทพูด (Talking Points)
def generate_talking_points(customer_name, mission_df):
    try:
        if "GROQ_API_KEY" not in st.secrets:
            return "⚠️ กรุณาใส่ GROQ_API_KEY"

        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        
        tasks_text = ""
        if not mission_df.empty:
            tasks_list = [f"- {row['topic']}: {row['desc']}" for _, row in mission_df.iterrows()]
            tasks_text = "\n".join(tasks_list)
        else:
            tasks_text = "เยี่ยมเยียนทั่วไป"

        prompt = f"""
        Role: ผู้ช่วยเซลล์มืออาชีพ
        Customer: {customer_name}
        Mission: {tasks_text}
        
        Output:
        1. Ice Breaker (1 ประโยค): ทักทายเปิดบทสนทนา
        2. Talking Points (3 ข้อ): ประเด็นที่จะคุยเพื่อให้บรรลุ Mission
        (ปรับโทนเสียงตามบริบท: ถ้าเป็นงานเลี้ยงให้เน้นความสัมพันธ์, ถ้างานขายให้เน้นข้อมูล)
        """
        
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=500
        )
        return completion.choices[0].message.content
    except Exception as e:
        return f"AI Error: {str(e)}"

# 3.2 [ใหม่] ฟังก์ชันตรวจการบ้าน (Strict Auditor)
# ==========================================
# ฟังก์ชัน AI ตรวจการบ้าน (Smart Auditor - ปรับปรุงใหม่)
# ==========================================
def validate_mission_compliance(topic, desc, report_text):
    try:
        if "GROQ_API_KEY" not in st.secrets:
            return "⚠️ No Key", "gray"

        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        
        prompt = f"""
        Role: คุณคือ "ผู้ตรวจสอบข้อมูล" (Auditor) ที่มีวิจารณญาณดีเยี่ยม
        Task: ตรวจสอบว่า "รายงานของเซลล์" ตอบโจทย์ "คำสั่ง" ได้สมเหตุสมผลหรือไม่
        
        ---
        คำสั่ง (Mission): {topic} ({desc})
        รายงาน (Report): "{report_text}"
        ---
        
        กฎการตัดสิน (Criteria - Flexible):
        1. **Timeframe:** ให้ยอมรับคำที่ความหมายใกล้เคียงกันได้ (เช่น ปลายปี = ธ.ค., ปีหน้า = ม.ค. เป็นต้น)
        2. **Substance:** ถ้าเซลล์ให้ "ข้อมูลใหม่" หรือ "คำอธิบาย" ที่เกี่ยวข้องกับโจทย์ แม้จะเป็นข่าวร้ายหรือปฏิเสธ ก็ถือว่า **PASS** (เพราะถือว่าได้ไปถามมาแล้ว)
        3. **Completeness:** ให้ FAIL เฉพาะกรณีที่ "ไม่ได้พูดถึงเรื่องนั้นเลย" หรือ "ตอบคนละเรื่อง" เท่านั้น
        
        Output Format:
        [PASS/FAIL]: [เหตุผลสั้นๆ]
        """
        
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1, # เพิ่มความยืดหยุ่นนิดนึง (จาก 0 เป็น 0.1)
            max_tokens=150
        )
        result = completion.choices[0].message.content
        
        if "PASS" in result: return result, "green"
        else: return result, "red"
            
    except Exception as e:
        return f"Error: {e}", "gray"

# ==========================================
# 4. LOAD DATA
# ==========================================
try:
    df_assignments = get_data("Assignments")
    df_missions = get_data("Missions")
except:
    st.stop()

# State Management
if 'report_text_buffer' not in st.session_state:
    st.session_state.report_text_buffer = ""
if 'sales_checklist' not in st.session_state:
    st.session_state.sales_checklist = set()
if 'audit_results' not in st.session_state:
    st.session_state.audit_results = {}

# ==========================================
# 5. UI ROUTING
# ==========================================
user_role = st.sidebar.radio("Login Role:", ("Sales Manager", "Sales Rep"))

if st.sidebar.button("🔄 Refresh Data"):
    st.cache_data.clear()
    st.session_state.report_text_buffer = ""
    st.session_state.sales_checklist = set()
    st.session_state.audit_results = {}
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
            st.info("ยังไม่มีรายงาน")

# --- SALES ROLE ---
else:
    st.header("📱 Sales App")
    
    # 1. Login
    sales_list = df_assignments['Sales_Rep'].unique() if not df_assignments.empty else []
    current_user = st.selectbox("👤 Login:", sales_list)
    
    my_custs = []
    if not df_assignments.empty and current_user:
        my_custs = df_assignments[df_assignments['Sales_Rep'] == current_user]['Customer'].unique()
    
    st.divider()
    target_cust = st.selectbox("🏢 เลือกลูกค้าที่เข้าเยี่ยม:", my_custs)
    
    # Logic รีเซ็ตเมื่อเปลี่ยนลูกค้า
    if 'last_cust' not in st.session_state:
        st.session_state.last_cust = target_cust
    if st.session_state.last_cust != target_cust:
        st.session_state.report_text_buffer = ""
        st.session_state.sales_checklist = set()
        st.session_state.audit_results = {}
        st.session_state.last_cust = target_cust

    # 3. ดึง Mission
    my_missions = pd.DataFrame()
    if not df_missions.empty and 'Customer' in df_missions.columns:
        my_missions = df_missions[df_missions['Customer'] == target_cust]

    # [AI Talking Points]
    with st.expander("✨ ให้ AI ช่วยคิดบทพูด (Talking Points)", expanded=False):
        if st.button("💡 กดเพื่อให้ AI วิเคราะห์โจทย์"):
            with st.spinner("AI กำลังวางแผนการขาย..."):
                ai_advice = generate_talking_points(target_cust, my_missions)
                st.markdown(ai_advice)
    
    st.divider()

    # 4. Mission Checklist & Reporting Area
    if my_missions.empty:
        st.success("🎉 ไม่มีงานค้าง (All Clear)")
    else:
        st.subheader(f"📋 งานที่ต้องทำ: {target_cust}")
        
        # === ส่วนรายงานรวม (Voice/Text) ===
        st.caption("🎙️ พูดรายงานรวม หรือพิมพ์ทีละข้อก็ได้")
        
        col_mic, col_text = st.columns([1, 4])
        with col_mic:
            st.write("")
            audio = mic_recorder(start_prompt="🎙️ พูด", stop_prompt="⏹️ หยุด", key="main_mic", format="webm", use_container_width=True)
        
        with col_text:
            if audio:
                with st.spinner("แปลงเสียง..."):
                    text = transcribe_audio(audio['bytes'])
                    if text:
                        if st.session_state.report_text_buffer:
                            st.session_state.report_text_buffer += " " + text
                        else:
                            st.session_state.report_text_buffer = text
            
            main_report_text = st.text_area("📝 รายงานผลรวม:", value=st.session_state.report_text_buffer, height=100)
            st.session_state.report_text_buffer = main_report_text

        st.divider()

        # === ส่วนตรวจสอบรายข้อ (Auditor) ===
        checklist_status = st.session_state.sales_checklist
        
        for index, row in my_missions.iterrows():
            topic = row['topic']
            desc = row['desc']
            is_done = topic in checklist_status
            
            icon = "✅" if is_done else "🔴"
            
            with st.expander(f"{icon} **{topic}**: {desc}", expanded=not is_done):
                
                c1, c2 = st.columns([3, 1])
                with c1:
                    st.info(f"**ต้องทำ:** {desc}")
                with c2:
                    if st.button(f"🔍 ตรวจคำตอบ", key=f"chk_{index}"):
                        if not main_report_text:
                            st.warning("พูดรายงานก่อนครับ")
                        else:
                            with st.spinner("AI กำลังจับผิด..."):
                                result, color = validate_mission_compliance(topic, desc, main_report_text)
                                st.session_state.audit_results[topic] = (result, color)
                                if color == "green":
                                    checklist_status.add(topic)
                                    st.session_state.sales_checklist = checklist_status
                                    st.rerun()
                
                # Show Result
                if topic in st.session_state.audit_results:
                    res_text, res_color = st.session_state.audit_results[topic]
                    if res_color == "green":
                        st.success(res_text)
                    else:
                        st.error(res_text)

        # === Submit ===
        completed_count = len(checklist_status)
        total_count = len(my_missions)
        
        st.write(f"**สถานะ:** {completed_count}/{total_count} ข้อ")
        
        if completed_count == total_count:
            st.success("ครบถ้วน! ปิดงานได้เลย")
            if st.button("🚀 ปิดงาน (Save & Clear)", type="primary"):
                topics_str = ", ".join(checklist_status)
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                report_row = [
                    timestamp, 
                    current_user, 
                    target_cust, 
                    topics_str, 
                    "Completed", 
                    main_report_text
                ]
                
                append_data("Reports", report_row)
                delete_mission_from_sheet(target_cust)
                
                # Clear
                if target_cust in st.session_state.sales_checklist:
                    del st.session_state.sales_checklist
                st.session_state.report_text_buffer = "" 
                st.session_state.audit_results = {}
                
                st.toast("บันทึกเรียบร้อย!", icon="☁️")
                time.sleep(2)
                st.rerun()
        else:
            st.warning("ต้องผ่านการตรวจสอบครบทุกข้อก่อน จึงจะปิดงานได้")