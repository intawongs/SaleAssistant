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

st.set_page_config(page_title="RC Sales AI (Smart Summary)", layout="wide", page_icon="✨")

# ==========================================
# 1. GOOGLE SHEETS & SETUP
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
# 2. VOICE TO TEXT
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

# ==========================================
# 3. AI BRAIN (Groq / Llama 3)
# ==========================================

# 3.1 ช่วยสรุปความ (Summarizer) - พระเอกของเรา
def summarize_voice_report(raw_text, customer_name):
    try:
        if "GROQ_API_KEY" not in st.secrets: return raw_text
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        
        prompt = f"""
        Role: คุณคือเลขาฯ มืออาชีพ ที่เก่งในการสรุปรายงานการประชุม
        Task: เรียบเรียง "คำพูดของเซลล์" ให้เป็น "รายงานสรุปผลการเข้าพบลูกค้า" ที่กระชับ เป็นทางการ และอ่านง่าย
        
        ข้อมูลดิบ (สิ่งที่เซลล์พูด): "{raw_text}"
        ลูกค้า: {customer_name}
        
        คำสั่ง:
        1. สรุปใจความสำคัญเป็นข้อๆ (Bullet Points)
        2. ใช้ภาษาเขียนแบบธุรกิจ (Business Language) ตัดคำฟุ่มเฟือยออก
        3. ถ้ามีตัวเลข/วันที่/ราคา ต้องระบุให้ชัดเจน ห้ามตัดทิ้ง
        4. ไม่ต้องเกริ่นนำ ให้ใส่เนื้อหาเลย
        """
        
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500
        )
        return completion.choices[0].message.content
    except Exception as e:
        return f"(AI Error: {e}) {raw_text}"

# 3.2 ช่วยคิดบทพูด (Talking Points) - ตัวเดิม
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

# 3.3 Auto-Followup (สร้างงานต่อ) - ตัวเดิม
def create_followup_mission(customer, report_text):
    try:
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        today = datetime.datetime.now().strftime("%d/%m/%Y")
        prompt = f"""
        Role: CRM Automation
        Date: {today}
        Report: "{report_text}"
        Output JSON: {{ "create": true/false, "topic": "...", "desc": "...", "status": "pending" }}
        Rule: If specific date mentioned -> create mission. If rejected/general -> create monthly visit.
        """
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1, response_format={"type": "json_object"}
        )
        return json.loads(completion.choices[0].message.content)
    except:
        return {"create": True, "topic": "Monthly Visit", "desc": "System Auto-Gen", "status": "pending"}

# ==========================================
# 4. LOAD DATA
# ==========================================
try:
    df_assignments = get_data("Assignments")
    df_missions = get_data("Missions")
except: st.stop()

if 'summary_buffer' not in st.session_state: st.session_state.summary_buffer = ""
if 'raw_voice_buffer' not in st.session_state: st.session_state.raw_voice_buffer = ""

# ==========================================
# 5. UI
# ==========================================
user_role = st.sidebar.radio("Login Role:", ("Sales Manager", "Sales Rep"))

if st.sidebar.button("🔄 Refresh"):
    st.cache_data.clear()
    st.session_state.summary_buffer = ""
    st.session_state.raw_voice_buffer = ""
    st.session_state.talking_points_cache = None
    st.rerun()

# --- MANAGER ---
if user_role == "Sales Manager":
    st.header("👮 Manager Dashboard")
    t1, t2, t3 = st.tabs(["📝 สั่งงาน", "📂 งานค้าง", "📊 รายงานสรุป"])
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
    st.header("📱 Sales App (Voice Summary)")
    
    s_list = df_assignments['Sales_Rep'].unique() if not df_assignments.empty else []
    cur_user = st.selectbox("👤 Login:", s_list)
    
    my_custs = df_assignments[df_assignments['Sales_Rep'] == cur_user]['Customer'].unique() if not df_assignments.empty and cur_user else []
    st.divider()
    target_cust = st.selectbox("🏢 เลือกลูกค้า:", my_custs)
    
    # Reset Logic
    if 'last_cust' not in st.session_state: st.session_state.last_cust = target_cust
    if st.session_state.last_cust != target_cust:
        st.session_state.summary_buffer = ""
        st.session_state.raw_voice_buffer = ""
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
        if 'talking_points_cache' in st.session_state and st.session_state.talking_points_cache:
            st.info(st.session_state.talking_points_cache)
    
    st.divider()

    if my_missions.empty:
        st.success("🎉 ไม่มีงานค้าง")
    else:
        st.subheader(f"📋 โจทย์วันนี้: {target_cust}")
        # โชว์โจทย์เฉยๆ (ไม่ต้องติ๊ก)
        for i, row in my_missions.iterrows():
            st.info(f"🔹 **{row['topic']}**: {row['desc']}")

        st.divider()
        
        # === Voice Recorder ===
        st.write("🎙️ **รายงานผล (พูดได้เลยเดี๋ยว AI สรุปให้):**")
        
        col_mic, col_text = st.columns([1, 4])
        with col_mic:
            st.write("")
            audio = mic_recorder(start_prompt="🎙️ พูด", stop_prompt="⏹️ หยุด", key="mic", format="webm", use_container_width=True)
        
        with col_text:
            if audio:
                if 'last_audio' not in st.session_state: st.session_state.last_audio = None
                if audio['bytes'] != st.session_state.last_audio:
                    st.session_state.last_audio = audio['bytes']
                    
                    with st.spinner("กำลังแปลงเสียง และเรียบเรียงใหม่..."):
                        # 1. แปลงเสียงเป็น Text ดิบ
                        raw_text = transcribe_audio(audio['bytes'])
                        if raw_text:
                            st.session_state.raw_voice_buffer = raw_text # เก็บเสียงดิบไว้เผื่อดู
                            
                            # 2. ให้ AI สรุปความ (Smart Summary)
                            summary = summarize_voice_report(raw_text, target_cust)
                            st.session_state.summary_buffer = summary
                            st.rerun()

            # แสดงผลสรุป (แก้ไขได้)
            final_summary = st.text_area(
                "📝 ข้อความสรุปจาก AI (แก้ไขได้ถ้าไม่ตรงใจ):", 
                value=st.session_state.summary_buffer, 
                height=150,
                help="นี่คือข้อความที่จะถูกบันทึกลงระบบ"
            )
            st.session_state.summary_buffer = final_summary
            
            # (Optional) แอบโชว์ข้อความดิบเล็กๆ เผื่อ AI สรุปผิด
            if st.session_state.raw_voice_buffer:
                with st.expander("ดูข้อความเสียงต้นฉบับ (Raw Voice)"):
                    st.caption(st.session_state.raw_voice_buffer)

        # === Submit ===
        st.divider()
        if st.session_state.summary_buffer:
            if st.button("🚀 ยืนยันและปิดงาน (Save)", type="primary", use_container_width=True):
                
                ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                # บันทึก: [Timestamp, User, Customer, Topics(รวม), Status, Summary]
                mission_topics = ", ".join(my_missions['topic'].tolist())
                append_data("Reports", [ts, cur_user, target_cust, mission_topics, "Completed", final_summary])
                
                # Auto Follow-up จากเนื้อหาสรุป
                with st.spinner("กำลังวางแผนงานรอบหน้า..."):
                    followup = create_followup_mission(target_cust, final_summary)
                    if followup.get("create"):
                        append_data("Missions", [target_cust, followup['topic'], followup['desc'], "pending"])
                
                # Cleanup
                delete_mission_from_sheet(target_cust)
                st.session_state.summary_buffer = ""
                st.session_state.raw_voice_buffer = ""
                st.session_state.talking_points_cache = None
                
                st.toast("เรียบร้อย! ส่งรายงานแล้ว", icon="✅")
                time.sleep(2)
                st.rerun()
        else:
            st.button("🔒 ปิดงาน", disabled=True, use_container_width=True, help="กรุณาพูดรายงานก่อน")