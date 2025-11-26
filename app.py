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
import json

st.set_page_config(page_title="RC Sales AI (Hybrid Dynamic)", layout="wide", page_icon="🚀")

# ==========================================
# 1. GOOGLE SHEETS CONNECTION
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
    """อ่านข้อมูล (Cache 60 วินาที)"""
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
    """บันทึกข้อมูลและล้าง Cache"""
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
# 2. VOICE TRANSCRIPTION
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

# 3.1 ช่วยคิดบทพูด (Talking Points)
def generate_talking_points(customer_name, mission_df):
    try:
        if "GROQ_API_KEY" not in st.secrets: return "⚠️ กรุณาใส่ GROQ_API_KEY"
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        
        tasks_text = "\n".join([f"- {row['topic']}: {row['desc']}" for _, row in mission_df.iterrows()]) if not mission_df.empty else "เยี่ยมเยียนทั่วไป"
        
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": f"Role: Sales Coach\nCustomer: {customer_name}\nTasks: {tasks_text}\nOutput: Ice Breaker (1 sentence), Talking Points (3 bullets). Thai Language."}],
            temperature=0.7
        )
        return completion.choices[0].message.content
    except: return "AI Error"

# 3.2 [ใหม่] สร้างตัวเลือกคำตอบอัตโนมัติ (Dynamic Options)
# ==========================================
# 3.2 [UPDATED] สร้างตัวเลือกคำตอบอัตโนมัติ (เน้นมุมมองลูกค้า)
# ==========================================
@st.cache_data(show_spinner=False)
def get_dynamic_options(topic, desc):
    try:
        if "GROQ_API_KEY" not in st.secrets: return ["✅ ดี/ปกติ", "⚠️ กระทบปานกลาง", "❌ กระทบหนัก/แย่"]
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        
        prompt = f"""
        Role: Sales Expert creating a checklist.
        Task: สร้างตัวเลือกคำตอบ (Checklist) 3 ข้อ สำหรับให้เซลล์ติ๊กหลังจากถามลูกค้าเรื่อง "{topic}" ({desc})
        
        Constraint (สำคัญมาก):
        - ตัวเลือกต้องเป็น **"สิ่งที่ลูกค้าตอบ"** หรือ **"สถานการณ์จริงของลูกค้า"** (Customer Reaction)
        - **ห้าม** เอาทฤษฎีเศรษฐศาสตร์มาตอบ
        - ภาษา: ไทย (สั้น กระชับ แบบภาษาพูดเซลล์)
        
        Structure (3 ตัวเลือก):
        1. Positive/No Problem (ลูกค้าโอเค/ไม่กระทบ/ได้ประโยชน์)
        2. Neutral/Wait (รอดูก่อน/กระทบนิดหน่อย)
        3. Negative/Problem (ลูกค้าบ่น/กระทบหนัก/ชะลอซื้อ)
        
        Example Case:
        Input: ค่าเงินบาทแข็ง
        Output: ไม่กระทบ(ทำ Forward ไว้), กระทบนิดหน่อย, กระทบหนัก(กำไรหาย)
        
        Input: สินค้าใหม่
        Output: สนใจสั่งเลย, ขอตัวอย่างลองก่อน, ไม่สนใจ/แพงไป
        
        Real Input: {topic}
        Output Format: Just 3 options separated by comma (,)
        """
        
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=60
        )
        options = completion.choices[0].message.content.split(',')
        clean_options = [opt.strip() for opt in options if opt.strip()]
        
        if len(clean_options) < 3: return ["✅ ลูกค้าโอเค/ไม่กระทบ", "⚠️ กระทบปานกลาง", "❌ กระทบหนัก/ชะลอออเดอร์"]
        return clean_options
    except:
        return ["✅ ลูกค้าโอเค/ไม่กระทบ", "⚠️ กระทบปานกลาง", "❌ กระทบหนัก/ชะลอออเดอร์"]

# 3.3 สร้างงานติดตามผลอัตโนมัติ (Auto-Followup)
# ==========================================
# 3.3 สร้างงานติดตามผลอัตโนมัติ (Auto-Followup) - [UPDATED]
# ==========================================
def create_followup_mission(customer, report_text, manual_status):
    try:
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        today = datetime.datetime.now().strftime("%d/%m/%Y")
        
        prompt = f"""
        Role: ระบบ CRM อัตโนมัติที่ฉลาดและแม่นยำเรื่องเวลา
        Date Today: {today}
        
        Input Data:
        1. Customer: {customer}
        2. Voice Report: "{report_text}"
        3. Checkbox Status: "{manual_status}"
        
        คำสั่ง: สร้างภารกิจถัดไป (Next Mission) โดยยึดกฎลำดับความสำคัญดังนี้ (Priority):
        
        🚨 Priority 1 (สูงสุด): ค้นหา "เวลา/วันที่/เดือน" ใน Voice Report ก่อน
        - ถ้าเจอคำว่า "มกรา", "เดือนหน้า", "ปีหน้า", "อีก 2 เดือน", "วันที่ 15"
        - **ต้องสร้าง** Mission ให้สอดคล้องกับเวลานั้น (เช่น Follow up: ตามเรื่องเดิม (นัดไว้ ม.ค.))
        - ห้ามใช้ Default 2 สัปดาห์เด็ดขาดถ้าเจอเวลาในเสียง
        
        🚨 Priority 2: ถ้าในเสียงไม่มีเวลา ค่อยดู Checkbox Status
        - ถ้า Status = "รอสรุป" -> ให้ตามต่อใน 14 วัน
        - ถ้า Status = "จบงาน/ขายได้" -> ให้เยี่ยมเดือนหน้า (Check Satisfaction)
        - ถ้า Status = "ไม่สนใจ" -> ให้เยี่ยมเดือนหน้า (Keep Contact)
        
        Output Format (JSON):
        {{
            "create": true,
            "topic": "หัวข้อภารกิจ (ระบุเดือน/เวลาในวงเล็บถ้ามี)",
            "desc": "รายละเอียดสิ่งที่ต้องทำ (ดึงมาจาก Voice Report)",
            "status": "pending"
        }}
        """
        
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1, # ลดความมั่ว ให้ทำตามกฎเป๊ะๆ
            response_format={"type": "json_object"}
        )
        return json.loads(completion.choices[0].message.content)
    except Exception as e:
        # Fallback กรณี AI เอ๋อ
        return {"create": True, "topic": "Follow up (System Auto)", "desc": f"ติดตามงานต่อจากรายงาน: {report_text}", "status": "pending"}
# ==========================================
# 4. LOAD DATA & STATE
# ==========================================
try:
    df_assignments = get_data("Assignments")
    df_missions = get_data("Missions")
except: st.stop()

# [FIXED] Initialize Session State ให้ครบทุกตัว
if 'report_text_buffer' not in st.session_state: st.session_state.report_text_buffer = ""
if 'mission_results' not in st.session_state: st.session_state.mission_results = {} 
if 'talking_points_cache' not in st.session_state: st.session_state.talking_points_cache = None # <--- ตัวปัญหา แก้แล้ว

# ==========================================
# 5. UI ROUTING
# ==========================================
user_role = st.sidebar.radio("Login Role:", ("Sales Manager", "Sales Rep"))

if st.sidebar.button("🔄 Refresh"):
    st.cache_data.clear()
    st.session_state.report_text_buffer = ""
    st.session_state.mission_results = {}
    st.session_state.talking_points_cache = None
    st.rerun()

# --- MANAGER ROLE ---
if user_role == "Sales Manager":
    st.header("👮 Manager Dashboard")
    t1, t2, t3 = st.tabs(["📝 สั่งงาน", "📂 งานค้าง", "📊 รายงาน"])
    
    with t1:
        c1, c2 = st.columns(2)
        with c1:
            s_list = df_assignments['Sales_Rep'].unique() if not df_assignments.empty else []
            sel_sale = st.selectbox("Sales Rep", s_list)
            c_list = df_assignments[df_assignments['Sales_Rep'] == sel_sale]['Customer'].unique() if not df_assignments.empty and sel_sale else []
            sel_cust = st.selectbox("Customer", c_list)
        with c2:
            topic = st.text_input("หัวข้องาน")
            desc = st.text_input("รายละเอียด")
            if st.button("➕ บันทึก", type="primary"):
                if topic and sel_cust:
                    append_data("Missions", [sel_cust, topic, desc, "pending"])
                    st.success(f"สั่งงาน {sel_cust} แล้ว!")
                    time.sleep(1)
                    st.rerun()
    with t2: st.dataframe(df_missions)
    with t3: 
        try: st.dataframe(get_data("Reports"))
        except: st.info("No Data")

# --- SALES ROLE ---
else:
    st.header("📱 Sales App (Hybrid Mode)")
    
    # 1. Login
    s_list = df_assignments['Sales_Rep'].unique() if not df_assignments.empty else []
    cur_user = st.selectbox("👤 Login:", s_list)
    
    my_custs = df_assignments[df_assignments['Sales_Rep'] == cur_user]['Customer'].unique() if not df_assignments.empty and cur_user else []
    st.divider()
    target_cust = st.selectbox("🏢 เลือกลูกค้า:", my_custs)
    
    # Reset Logic
    if 'last_cust' not in st.session_state: st.session_state.last_cust = target_cust
    if st.session_state.last_cust != target_cust:
        st.session_state.report_text_buffer = ""
        st.session_state.mission_results = {}
        st.session_state.talking_points_cache = None
        st.session_state.last_cust = target_cust

    my_missions = pd.DataFrame()
    if not df_missions.empty and 'Customer' in df_missions.columns:
        my_missions = df_missions[df_missions['Customer'] == target_cust]

    # AI Coach
    with st.expander("✨ ให้ AI ช่วยคิดบทพูด (Talking Points)", expanded=False):
        if st.button("💡 วิเคราะห์โจทย์"):
            with st.spinner("Thinking..."):
                ai_advice = generate_talking_points(target_cust, my_missions)
                st.session_state.talking_points_cache = ai_advice
        
        if st.session_state.talking_points_cache:
            st.info(st.session_state.talking_points_cache)
    
    st.divider()

    if my_missions.empty:
        st.success("🎉 ไม่มีงานค้าง")
    else:
        st.subheader(f"📋 งานที่ต้องทำ: {target_cust}")
        
        # === 1. Voice Report (Overview) ===
        st.info("🎙️ 1. พูดรายงานภาพรวม / รายละเอียดเพิ่มเติม")
        c1, c2 = st.columns([1, 4])
        with c1:
            st.write("")
            # Key unique to prevent loop
            audio = mic_recorder(start_prompt="🎙️ พูด", stop_prompt="⏹️ หยุด", key="main_mic", format="webm", use_container_width=True)
        
        with c2:
            # Logic Override Voice
            if audio:
                if 'last_audio' not in st.session_state: st.session_state.last_audio = None
                if audio['bytes'] != st.session_state.last_audio:
                    st.session_state.last_audio = audio['bytes']
                    with st.spinner("กำลังพิมพ์..."):
                        text = transcribe_audio(audio['bytes'])
                        if text: st.session_state.report_text_buffer = text
                        st.rerun()
            
            report_text = st.text_area("รายละเอียด:", value=st.session_state.report_text_buffer, height=100)
            st.session_state.report_text_buffer = report_text

        st.divider()

        # === 2. Dynamic Checkboxes (AI Gen) ===
        st.info("✅ 2. สรุปสถานะ (AI สร้างตัวเลือกให้)")
        
        results_summary = []
        
        for i, row in my_missions.iterrows():
            topic = row['topic']
            desc = row['desc']
            
            with st.container(border=True):
                st.markdown(f"**ภารกิจ: {topic}**")
                st.caption(desc)
                
                # AI Generate Options (Cached)
                ai_options = get_dynamic_options(topic, desc)
                final_options = ["(เลือกผลลัพธ์)"] + ai_options
                
                # Retrieve prev state
                default_idx = 0
                if topic in st.session_state.mission_results:
                    prev_val = st.session_state.mission_results[topic]
                    if prev_val in final_options:
                        default_idx = final_options.index(prev_val)
                
                selection = st.radio(
                    f"ผลลัพธ์:", 
                    final_options, 
                    index=default_idx,
                    key=f"rad_{i}",
                    horizontal=True,
                    label_visibility="collapsed"
                )
                
                st.session_state.mission_results[topic] = selection
                
                if selection != "(เลือกผลลัพธ์)":
                    results_summary.append(selection)

        # === Submit ===
        st.divider()
        done_count = len(results_summary)
        total = len(my_missions)
        
        if done_count == total:
            if st.button("🚀 ปิดงาน (Save)", type="primary", use_container_width=True):
                
                # Format Report
                status_sum = "\n".join([f"- {k}: {v}" for k,v in st.session_state.mission_results.items()])
                final_log = f"DETAILS:\n{report_text}\n\nSTATUS:\n{status_sum}"
                ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                # Save to Google Sheets
                append_data("Reports", [ts, cur_user, target_cust, status_sum, "Completed", final_log])
                
                # Auto Follow-up
                main_status = results_summary[0] if results_summary else "General"
                with st.spinner("กำลังสร้างงานติดตามผล..."):
                    followup = create_followup_mission(target_cust, report_text, main_status)
                    if followup.get("create"):
                        append_data("Missions", [target_cust, followup['topic'], followup['desc'], "pending"])
                
                # Cleanup
                delete_mission_from_sheet(target_cust)
                st.session_state.mission_results = {}
                st.session_state.report_text_buffer = ""
                st.session_state.talking_points_cache = None
                
                st.toast("บันทึกเรียบร้อย!", icon="✅")
                time.sleep(2)
                st.rerun()
        else:
            st.warning(f"กรุณาเลือกสถานะให้ครบทุกข้อ ({done_count}/{total})")