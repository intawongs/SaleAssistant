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

st.set_page_config(page_title="RC Sales AI (Smart Date)", layout="wide", page_icon="📅")

# ==========================================
# 1. CONNECTIONS
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
        if not df.empty: df.columns = [str(c).strip() for c in df.columns]
        return df
    except: return pd.DataFrame()

def append_data(worksheet_name, row_data):
    try:
        client = init_connection()
        sheet = client.open(SHEET_NAME)
        worksheet = sheet.worksheet(worksheet_name)
        worksheet.append_row(row_data)
        st.cache_data.clear()
    except Exception as e: st.error(f"Save Error: {e}")

def delete_mission_from_sheet(customer_name):
    try:
        client = init_connection()
        sheet = client.open(SHEET_NAME)
        ws = sheet.worksheet("Missions")
        data = ws.get_all_records()
        rows_to_delete = [i + 2 for i, row in enumerate(data) if row.get('Customer') == customer_name]
        for r in reversed(rows_to_delete): ws.delete_rows(r)
        st.cache_data.clear()
    except Exception as e: st.error(f"Delete Error: {e}")

# ==========================================
# 2. UTILITIES
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
    except: return None

# [ฟังก์ชันแยกงาน วันนี้ vs อนาคต ยังคงใช้ Logic Python เดิมเพราะแม่นยำเรื่องการเปรียบเทียบวัน]
def get_task_status_by_date(topic_str):
    import re
    try:
        # หา Pattern (7 ธ.ค.) หรือ (1 มกราคม)
        match = re.search(r"\(\s*(\d+)\s+([ก-๙.]+)\s*\)", topic_str)
        if not match: return 'today' # ไม่ระบุวัน = ทำเลย
        
        day = int(match.group(1))
        month_str = match.group(2)
        
        thai_months = {"ม.ค.":1,"มกราคม":1,"ก.พ.":2,"กุมภาพันธ์":2,"มี.ค.":3,"มีนาคม":3,"เม.ย.":4,"เมษายน":4,"พ.ค.":5,"พฤษภาคม":5,"มิ.ย.":6,"มิถุนายน":6,"ก.ค.":7,"กรกฎาคม":7,"ส.ค.":8,"สิงหาคม":8,"ก.ย.":9,"กันยายน":9,"ต.ค.":10,"ตุลาคม":10,"พ.ย.":11,"พฤศจิกายน":11,"ธ.ค.":12,"ธันวาคม":12}
        
        month = 0
        for k,v in thai_months.items():
            if k in month_str: month = v; break
        if month == 0: return 'today'

        today = datetime.date.today()
        year = today.year
        # ถ้าเดือนที่ระบุ น้อยกว่าเดือนปัจจุบัน (เช่น ตอนนี้ธันวา สั่งงานมกรา) ให้ปัดเป็นปีหน้า
        if month < today.month: year += 1
        
        task_date = datetime.date(year, month, day)
        
        return 'future' if task_date > today else 'today'
    except: return 'today'

# ==========================================
# 3. AI LOGIC (Groq) - ปรับปรุงใหม่
# ==========================================

# 3.1 สรุปความ (สั้น กระชับ ได้ใจความ)
# ==========================================
# 3.1 สรุปความ (Smart Summarizer - แก้คำพูดและตัดส่วนเกิน)
# ==========================================
def summarize_voice_report(raw_text, customer_name):
    try:
        if "GROQ_API_KEY" not in st.secrets: return raw_text
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        
        prompt = f"""
        Context: นี่คือคำพูดของ "Sales Rep" ที่กำลังรายงานผลการเยี่ยมลูกค้าชื่อ "{customer_name}" ส่งให้หัวหน้าอ่าน
        Input Text: "{raw_text}"
        
        คำสั่ง (Strict Rules):
        1. **แก้ไขประธานให้ถูก:** ให้สรุปว่า "ลูกค้าแจ้งว่า..." หรือ "ลูกค้าต้องการ..." (ห้ามใช้คำว่า ลูกค้าได้รับแจ้ง)
        2. **ตัดส่วนเกินทิ้ง (No Filler):** - ห้ามใส่ประโยคพวก "ไม่มีข้อมูลเพิ่มเติม", "หากต้องการรายละเอียดติดต่อ..."
           - ห้ามขึ้นต้นว่า "สรุปรายงาน..." ให้ใส่เนื้อหาเลย
        3. **เน้นเนื้อหา (Facts Only):** เก็บวันที่ (เช่น 7 ธ.ค.), ตัวเลข, ราคา ให้ครบถ้วน
        4. **ความยาว:** ขอสั้นๆ กระชับที่สุด ไม่เกิน 2 บรรทัด
        
        ตัวอย่าง Output ที่ต้องการ:
        "ลูกค้าแจ้งว่าจะคอนเฟิร์มออเดอร์ภายในวันที่ 7 ธ.ค. นี้"
        """
        
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1, # ใช้ 0.1 เพื่อความแม่นยำ ไม่แต่งเติม
            max_tokens=200
        )
        return completion.choices[0].message.content
    except: return raw_text

# 3.2 Auto-Followup (AI ฉลาดเลือกวัน)
# ==========================================
# 3.2 Auto-Followup (Logic ลำดับขั้น: วัน -> เดือน -> ไตรมาส)
# ==========================================
def create_followup_mission(customer, report_text):
    try:
        # เช็ค Key
        if "GROQ_API_KEY" not in st.secrets: return {"create": False}
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        
        today = datetime.datetime.now().strftime("%d/%m/%Y")
        
        prompt = f"""
        Role: ระบบ Scheduler อัจฉริยะ
        Date Today: {today}
        Input: "{report_text}"
        
        ภารกิจ: สร้างหัวข้อภารกิจใหม่ (Topic) โดยตรวจสอบเงื่อนไขตามลำดับดังนี้ (Check in Order):
        
        🔻 STEP 1: เช็คว่ามี "เลขวันที่" หรือไม่? (Priority สูงสุด)
           - คำที่หา: "วันที่ 7", "7 ธ.ค.", "ภายในวันที่ 15", "สิ้นเดือน" (ตีเป็น 30/31)
           - ถ้าเจอ: ให้ใช้เลขวันที่นั้นทันที
           - Topic: "Follow up ([เลขวันที่] [เดือน])" -> จบการทำงาน
           
        🔻 STEP 2: (ถ้าไม่เจอข้อ 1) เช็คว่ามี "ชื่อเดือน" หรือไม่?
           - คำที่หา: "มกราคม", "เดือนหน้า", "กุมภาพันธ์"
           - ถ้าเจอ: ให้กำหนดเป็น "วันที่ 1" ของเดือนนั้น
           - Topic: "Follow up (1 [เดือน])" -> จบการทำงาน
           
        🔻 STEP 3: (ถ้าไม่เจอข้อ 2) เช็คว่ามี "ไตรมาส" หรือไม่?
           - คำที่หา: "Q1", "ไตรมาส 2", "ต้นปีหน้า"
           - ถ้าเจอ: ให้กำหนดเป็น "วันที่ 1" ของเดือนแรกในไตรมาสนั้น
           - Topic: "Follow up (1 [เดือนแรกของไตรมาส])" -> จบการทำงาน
           
        🔻 STEP 4: (ถ้าไม่เจออะไรเลย)
           - Topic: "Monthly Visit"
        
        Output JSON Format:
        {{
            "create": true,
            "topic": "...",
            "desc": "แจ้งเตือนจากรายงาน: {report_text}",
            "status": "pending"
        }}
        """
        
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}], 
            temperature=0.0, # บังคับให้ทำตาม Step เป๊ะๆ
            response_format={"type": "json_object"}
        )
        return json.loads(completion.choices[0].message.content)
        
    except Exception as e:
        return {"create": True, "topic": "Monthly Visit", "desc": "Auto-Gen", "status": "pending"}

# 3.3 AI Coach
def generate_talking_points(customer, mission_df):
    try:
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        tasks = "\n".join([f"- {row['topic']}: {row['desc']}" for _, row in mission_df.iterrows()])
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": f"Role: Sales Coach\nCustomer: {customer}\nTask: {tasks}\nOutput: Ice Breaker (1), Talking Points (3). Thai language."}],
            temperature=0.7
        )
        return completion.choices[0].message.content
    except: return "..."

# ==========================================
# 4. UI & LOGIC
# ==========================================
try:
    df_assignments = get_data("Assignments")
    df_missions = get_data("Missions")
except: st.stop()

if 'report_text_buffer' not in st.session_state: st.session_state.report_text_buffer = ""
if 'raw_voice_buffer' not in st.session_state: st.session_state.raw_voice_buffer = ""
if 'talking_points_cache' not in st.session_state: st.session_state.talking_points_cache = None

user_role = st.sidebar.radio("Login Role:", ("Sales Manager", "Sales Rep"))

if st.sidebar.button("🔄 Refresh"):
    st.cache_data.clear()
    st.session_state.report_text_buffer = ""
    st.session_state.raw_voice_buffer = ""
    st.session_state.talking_points_cache = None
    st.rerun()

# --- MANAGER ---
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
            topic = st.text_input("หัวข้อ")
            desc = st.text_input("รายละเอียด")
            if st.button("➕ บันทึก", type="primary"):
                if topic and sel_cust:
                    append_data("Missions", [sel_cust, topic, desc, "pending"])
                    st.success("Saved!")
                    time.sleep(1)
                    st.rerun()
    with t2: st.dataframe(df_missions)
    with t3: 
        try: st.dataframe(get_data("Reports"))
        except: st.info("No Data")

# --- SALES REP ---
else:
    st.header("📱 Sales App")
    s_list = df_assignments['Sales_Rep'].unique() if not df_assignments.empty else []
    cur_user = st.selectbox("👤 Login:", s_list)
    my_custs = df_assignments[df_assignments['Sales_Rep'] == cur_user]['Customer'].unique() if not df_assignments.empty and cur_user else []
    
    st.divider()
    target_cust = st.selectbox("🏢 เลือกลูกค้า:", my_custs)
    
    if 'last_cust' not in st.session_state: st.session_state.last_cust = target_cust
    if st.session_state.last_cust != target_cust:
        st.session_state.report_text_buffer = ""
        st.session_state.raw_voice_buffer = ""
        st.session_state.talking_points_cache = None
        st.session_state.last_cust = target_cust

    my_missions = pd.DataFrame()
    if not df_missions.empty and 'Customer' in df_missions.columns:
        my_missions = df_missions[df_missions['Customer'] == target_cust]

    today_missions = []
    future_missions = []
    for _, row in my_missions.iterrows():
        if get_task_status_by_date(row['topic']) == 'today': today_missions.append(row)
        else: future_missions.append(row)
    
    df_today = pd.DataFrame(today_missions)
    df_future = pd.DataFrame(future_missions)

    with st.expander("✨ ให้ AI ช่วยคิดบทพูด (Talking Points)", expanded=False):
        if st.button("💡 วิเคราะห์โจทย์"):
            with st.spinner("Thinking..."):
                ai_advice = generate_talking_points(target_cust, df_today)
                st.session_state.talking_points_cache = ai_advice
        if st.session_state.talking_points_cache: st.info(st.session_state.talking_points_cache)
    
    st.divider()

    # === TODAY MISSION ===
    if df_today.empty:
        st.success("🎉 วันนี้ไม่มีงานค้าง (All Clear)")
    else:
        st.subheader(f"🔥 งานวันนี้ ({len(df_today)}):")
        for _, row in df_today.iterrows():
            st.info(f"🔹 **{row['topic']}**: {row['desc']}")
        
        st.divider()
        st.write("🎙️ **รายงานผล (AI สรุปให้):**")
        
        c1, c2 = st.columns([1, 4])
        with c1:
            st.write("")
            audio = mic_recorder(start_prompt="🎙️ พูด", stop_prompt="⏹️ หยุด", key="mic", format="webm", use_container_width=True)
        with c2:
            if audio:
                if 'last_audio' not in st.session_state: st.session_state.last_audio = None
                if audio['bytes'] != st.session_state.last_audio:
                    st.session_state.last_audio = audio['bytes']
                    with st.spinner("กำลังสรุปสั้นๆ..."):
                        raw_text = transcribe_audio(audio['bytes'])
                        if raw_text:
                            st.session_state.raw_voice_buffer = raw_text
                            summary = summarize_voice_report(raw_text, target_cust)
                            st.session_state.report_text_buffer = summary
                            st.rerun()
            
            final_report = st.text_area("📝 สรุป:", value=st.session_state.report_text_buffer, height=150)
            st.session_state.report_text_buffer = final_report
            
            if st.session_state.raw_voice_buffer:
                with st.expander("ดูเสียงต้นฉบับ"): st.caption(st.session_state.raw_voice_buffer)

        if st.session_state.report_text_buffer:
            if st.button("🚀 ปิดงาน (Save)", type="primary", use_container_width=True):
                ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                topics = ", ".join(df_today['topic'].tolist())
                
                append_data("Reports", [ts, cur_user, target_cust, topics, "Completed", final_report])
                delete_mission_from_sheet(target_cust)
                
                with st.spinner("Generating Next Mission..."):
                    fup = create_followup_mission(target_cust, final_report)
                    if fup.get("create"):
                        append_data("Missions", [target_cust, fup['topic'], fup['desc'], "pending"])
                        st.toast(f"Next: {fup['topic']}", icon="📅")
                
                st.session_state.report_text_buffer = ""
                st.session_state.raw_voice_buffer = ""
                st.session_state.talking_points_cache = None
                time.sleep(2)
                st.rerun()
        else:
            st.button("🔒 ปิดงาน", disabled=True, use_container_width=True)

    # === FUTURE MISSION ===
    if not df_future.empty:
        st.markdown("---")
        st.subheader(f"📅 งานในอนาคต ({len(df_future)}):")
        for _, row in df_future.iterrows():
            st.caption(f"🔜 {row['topic']} ({row['desc']})")