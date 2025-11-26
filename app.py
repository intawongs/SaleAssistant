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
import re

st.set_page_config(page_title="RC Sales AI (Sentiment)", layout="wide", page_icon="🌡️")

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

def get_task_status_by_date(topic_str):
    try:
        if not isinstance(topic_str, str): return 'today'
        today = datetime.date.today()
        match_digit = re.search(r"(\d{1,2})\s*[\/\-]\s*(\d{1,2})\s*[\/\-]\s*(\d{4})", topic_str)
        if match_digit:
            d, m, y = map(int, match_digit.groups())
            if y > 2400: y -= 543
            task_date = datetime.date(y, m, d)
            return 'future' if task_date > today else 'today'
        match_thai = re.search(r"(\d{1,2})\s+([ก-๙.]+)", topic_str)
        if match_thai:
            day = int(match_thai.group(1))
            month_str = match_thai.group(2)
            thai_months = {"ม.ค.":1,"มกราคม":1,"ก.พ.":2,"กุมภาพันธ์":2,"มี.ค.":3,"มีนาคม":3,"เม.ย.":4,"เมษายน":4,"พ.ค.":5,"พฤษภาคม":5,"มิ.ย.":6,"มิถุนายน":6,"ก.ค.":7,"กรกฎาคม":7,"ส.ค.":8,"สิงหาคม":8,"ก.ย.":9,"กันยายน":9,"ต.ค.":10,"ตุลาคม":10,"พ.ย.":11,"พฤศจิกายน":11,"ธ.ค.":12,"ธันวาคม":12}
            month = 0
            for k,v in thai_months.items():
                if k in month_str: month = v; break
            if month > 0:
                year = today.year
                if month < today.month: year += 1
                try:
                    task_date = datetime.date(year, month, day)
                    return 'future' if task_date > today else 'today'
                except: return 'today'
        return 'today'
    except: return 'today'

# ==========================================
# 3. AI LOGIC (Groq)
# ==========================================

# 3.1 สรุปความ
def summarize_voice_report(raw_text, customer_name, mission_df):
    try:
        if "GROQ_API_KEY" not in st.secrets: return raw_text
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        tasks_text = "\n".join([f"- {row['topic']}: {row['desc']}" for _, row in mission_df.iterrows()]) if not mission_df.empty else "ทั่วไป"
        prompt = f"""
        Task: สรุปรายงานการขาย (จับคู่โจทย์-คำตอบ)
        Input: "{raw_text}"
        Questions: {tasks_text}
        Rules: Bullet Points format, No filler words, Keep numbers/dates.
        """
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1, max_tokens=300
        )
        return completion.choices[0].message.content
    except: return raw_text

# 3.2 [NEW] วิเคราะห์ Sentiment (โอกาสการขาย)
def analyze_sentiment(report_text):
    try:
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        prompt = f"""
        Role: Sales Analyst
        Task: วิเคราะห์ "โอกาสในการขาย/ความสำเร็จ" จากรายงานนี้
        Report: "{report_text}"
        
        เกณฑ์การให้คะแนน (Sentiment):
        🟢 Positive: ลูกค้าสนใจ, สั่งซื้อแล้ว, นัดวันชัดเจน, ตอบรับดี, ไม่มีปัญหา
        🟡 Neutral: ลูกค้าขอคิดดูก่อน, ยังไม่ตัดสินใจ, รอผู้ใหญ่, รอดูงบ (กลางๆ)
        🔴 Negative: ลูกค้าปฏิเสธ, ไม่สนใจ, มีปัญหา, สินค้าไม่ตอบโจทย์, ราคาแพงไป
        
        Output Format (เลือก 1 อันเท่านั้น):
        "🟢 Positive" หรือ "🟡 Neutral" หรือ "🔴 Negative"
        """
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant", # ใช้ตัวเล็กก็พอ เร็วดี
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0, 
            max_tokens=10
        )
        return completion.choices[0].message.content.strip()
    except: return "⚪ Unknown"

# 3.3 Auto-Followup
def create_followup_mission(customer, report_text):
    try:
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        today = datetime.datetime.now().strftime("%d/%m/%Y")
        prompt = f"""
        Role: Scheduler. Date: {today}. Input: "{report_text}"
        Task: Create NEXT mission (`create`: true).
        Priority Rules:
        1. Specific Date -> Topic: "Follow up ([Date])"
        2. Month Only -> Topic: "Follow up (1 [Month])"
        3. Quarter -> Topic: "Follow up (1 [First Month])"
        4. No Date -> Topic: "Monthly Visit"
        Output JSON: {{ "create": true, "topic": "...", "desc": "...", "status": "pending" }}
        """
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile", 
            messages=[{"role": "user", "content": prompt}], 
            temperature=0.1, response_format={"type": "json_object"}
        )
        return json.loads(completion.choices[0].message.content)
    except:
        return {"create": True, "topic": "Monthly Visit", "desc": "Auto-Gen", "status": "pending"}

# 3.4 AI Coach
def generate_talking_points(customer, mission_df):
    try:
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        tasks = "\n".join([f"- {row['topic']}: {row['desc']}" for _, row in mission_df.iterrows()])
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
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
        st.write("🎙️ **รายงานผล (พูดเลย):**")
        
        c1, c2 = st.columns([1, 4])
        with c1:
            st.write("")
            audio = mic_recorder(start_prompt="🎙️ พูด", stop_prompt="⏹️ หยุด", key="mic", format="webm", use_container_width=True)
        with c2:
            if audio:
                if 'last_audio' not in st.session_state: st.session_state.last_audio = None
                if audio['bytes'] != st.session_state.last_audio:
                    st.session_state.last_audio = audio['bytes']
                    with st.spinner("กำลังจับคู่คำตอบ..."):
                        raw_text = transcribe_audio(audio['bytes'])
                        if raw_text:
                            st.session_state.raw_voice_buffer = raw_text
                            summary = summarize_voice_report(raw_text, target_cust, df_today)
                            st.session_state.report_text_buffer = summary
                            st.rerun()
            
            final_report = st.text_area("📝 สรุปจาก AI (แก้ไขได้):", value=st.session_state.report_text_buffer, height=200)
            st.session_state.report_text_buffer = final_report
            
            if st.session_state.raw_voice_buffer:
                with st.expander("ดูข้อความเสียงต้นฉบับ"): st.caption(st.session_state.raw_voice_buffer)

        if st.session_state.report_text_buffer:
            if st.button("🚀 ปิดงาน (Save)", type="primary", use_container_width=True):
                ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                topics = ", ".join(df_today['topic'].tolist())
                
                # [NEW] เรียกใช้ Sentiment Analysis
                sentiment = analyze_sentiment(final_report)
                
                # Save Report (เพิ่ม Sentiment เข้าไปในลำดับที่ 6)
                # Columns: Timestamp, User, Customer, Topics, Status, Sentiment, Summary
                append_data("Reports", [ts, cur_user, target_cust, topics, "Completed", sentiment, final_report])
                
                delete_mission_from_sheet(target_cust)
                
                with st.spinner("Creating Next Mission..."):
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

    if not df_future.empty:
        st.markdown("---")
        st.subheader(f"📅 งานในอนาคต ({len(df_future)}):")
        for _, row in df_future.iterrows():
            st.caption(f"🔜 {row['topic']} ({row['desc']})")