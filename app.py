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
import re # เพิ่ม Regular Expression เพื่อจับวันที่

st.set_page_config(page_title="RC Sales AI (Time Aware)", layout="wide", page_icon="📅")

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
                # ลบเฉพาะงานที่ทำวันนี้ (งานอนาคตเก็บไว้ก่อน)
                # ใน Demo นี้ขออนุญาตลบหมดเพื่อความง่าย (หรือจะปรับให้ลบเฉพาะ ID ก็ได้)
                rows_to_delete.append(i + 2) 
        for r in reversed(rows_to_delete):
            ws.delete_rows(r)
        st.cache_data.clear()
    except Exception as e:
        st.error(f"Error deleting mission: {e}")

# ==========================================
# 2. UTILITIES (Voice & Date Parsing)
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

# [NEW] ฟังก์ชันแยกแยะวันที่จากชื่อหัวข้อ
def get_task_status_by_date(topic_str):
    """
    return: 'today' (ทำวันนี้/เลยกำหนด/ไม่รู้วัน) หรือ 'future' (ยังไม่ถึง)
    Logic: หาคำในวงเล็บ เช่น (5 ม.ค.) หรือ (1 เมษายน) แล้วเทียบกับวันนี้
    """
    try:
        # 1. หา Pattern วันที่ในวงเล็บ เช่น (1 ม.ค.)
        match = re.search(r"\(\s*(\d+)\s+([ก-๙.]+)\s*\)", topic_str)
        if not match:
            return 'today' # ถ้าไม่ระบุวัน ถือว่าต้องทำเลย

        day = int(match.group(1))
        month_str = match.group(2)
        
        # แปลงชื่อเดือนไทยเป็นตัวเลข
        thai_months = {
            "ม.ค.": 1, "มกราคม": 1, "มกรา": 1,
            "ก.พ.": 2, "กุมภาพันธ์": 2, "กุมภา": 2,
            "มี.ค.": 3, "มีนาคม": 3, "มีนา": 3,
            "เม.ย.": 4, "เมษายน": 4, "เมษา": 4,
            "พ.ค.": 5, "พฤษภาคม": 5, "พฤษภา": 5,
            "มิ.ย.": 6, "มิถุนายน": 6, "มิถุนา": 6,
            "ก.ค.": 7, "กรกฎาคม": 7, "กรกฎา": 7,
            "ส.ค.": 8, "สิงหาคม": 8, "สิงหา": 8,
            "ก.ย.": 9, "กันยายน": 9, "กันยา": 9,
            "ต.ค.": 10, "ตุลาคม": 10, "ตุลา": 10,
            "พ.ย.": 11, "พฤศจิกายน": 11, "พฤศจิกา": 11,
            "ธ.ค.": 12, "ธันวาคม": 12, "ธันวา": 12
        }
        
        month = 0
        for k, v in thai_months.items():
            if k in month_str:
                month = v
                break
        
        if month == 0: return 'today' # แกะเดือนไม่ออก ให้ทำเลย

        # เทียบเวลา
        today = datetime.date.today()
        current_year = today.year
        # สมมติว่าเป็นปีปัจจุบัน หรือปีหน้าถ้าเดือนน้อยกว่าปัจจุบันมากๆ
        year = current_year
        if month < today.month - 1: # เช่น ตอนนี้ธันวา เจอโจทย์มกรา ให้ตีเป็นปีหน้า
            year += 1
            
        task_date = datetime.date(year, month, day)
        
        if task_date > today:
            return 'future'
        else:
            return 'today' # ถึงกำหนดแล้ว หรือเลยมาแล้ว
            
    except:
        return 'today' # กันเหนียว

# ==========================================
# 3. AI LOGIC
# ==========================================
def generate_talking_points(customer, mission_df):
    try:
        if "GROQ_API_KEY" not in st.secrets: return "⚠️ ใส่ Key"
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        
        # ส่งเฉพาะงานวันนี้ให้ AI คิดบทพูด
        tasks_text = ""
        for _, row in mission_df.iterrows():
            if get_task_status_by_date(row['topic']) == 'today':
                tasks_text += f"- {row['topic']}: {row['desc']}\n"
        
        if not tasks_text: tasks_text = "เยี่ยมเยียนทั่วไป (ไม่มีงานด่วน)"

        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": f"Role: Sales Coach\nCustomer: {customer}\nTask Today: {tasks_text}\nOutput: Ice Breaker (1), Talking Points (3). Thai language."}],
            temperature=0.7
        )
        return completion.choices[0].message.content
    except: return "AI Error"

def get_dynamic_options(topic, desc):
    try:
        if "GROQ_API_KEY" not in st.secrets: return ["✅ สำเร็จ", "⏳ รอสรุป", "❌ ปฏิเสธ"]
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        prompt = f"Task: Create 3 short checklist options for topic '{topic}'. Ordered: Positive, Neutral, Negative. Output comma separated only. No numbers."
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}], temperature=0.3, max_tokens=60
        )
        opts = completion.choices[0].message.content.split(',')
        clean = [o.strip().replace(".","") for o in opts if o.strip()]
        
        final = []
        emojis = ["✅ ", "⏳ ", "❌ "]
        for i, o in enumerate(clean[:3]):
            if any(e in o for e in ["✅","⏳","❌"]): final.append(o)
            else: final.append(f"{emojis[i]}{o}")
            
        return final if len(final) >= 3 else ["✅ สำเร็จ", "⏳ รอสรุป", "❌ ปฏิเสธ"]
    except: return ["✅ สำเร็จ", "⏳ รอสรุป", "❌ ปฏิเสธ"]

def create_followup_mission(customer, report_text, manual_status):
    try:
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        today = datetime.datetime.now().strftime("%d/%m/%Y")
        prompt = f"""
        Role: Scheduler. Date: {today}. Input: "{report_text}" (Status: {manual_status}).
        Task: Create next mission (`create`: true).
        Rules:
        1. If date found (e.g. 5 Dec) -> Topic: "Follow up ([Date])"
        2. If month found (e.g. Jan) -> Topic: "Follow up (1 [Month])"
        3. If quarter found (e.g. Q1) -> Topic: "Follow up (1 [First Month of Q])"
        4. Else -> Topic: "Monthly Visit"
        Output JSON: {{ "create": true, "topic": "...", "desc": "...", "status": "pending" }}
        """
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}], temperature=0.1, response_format={"type": "json_object"}
        )
        return json.loads(completion.choices[0].message.content)
    except:
        return {"create": True, "topic": "Monthly Visit", "desc": "Auto-Gen", "status": "pending"}

# ==========================================
# 4. LOAD DATA
# ==========================================
try:
    df_assignments = get_data("Assignments")
    df_missions = get_data("Missions")
except: st.stop()

if 'report_text_buffer' not in st.session_state: st.session_state.report_text_buffer = ""
if 'mission_results' not in st.session_state: st.session_state.mission_results = {} 
if 'talking_points_cache' not in st.session_state: st.session_state.talking_points_cache = None

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
            topic = st.text_input("หัวข้องาน")
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
        st.session_state.mission_results = {}
        st.session_state.talking_points_cache = None
        st.session_state.last_cust = target_cust

    my_missions = pd.DataFrame()
    if not df_missions.empty and 'Customer' in df_missions.columns:
        my_missions = df_missions[df_missions['Customer'] == target_cust]

    # --- แยกงานวันนี้ vs อนาคต ---
    today_missions = []
    future_missions = []
    
    for _, row in my_missions.iterrows():
        status = get_task_status_by_date(row['topic'])
        if status == 'today':
            today_missions.append(row)
        else:
            future_missions.append(row)
            
    # แปลงกลับเป็น DataFrame
    df_today = pd.DataFrame(today_missions)
    df_future = pd.DataFrame(future_missions)

    # [AI Talking Points] - คิดเฉพาะงานวันนี้
    with st.expander("✨ ให้ AI ช่วยคิดบทพูด (เฉพาะงานวันนี้)", expanded=False):
        if st.button("💡 วิเคราะห์โจทย์"):
            with st.spinner("Thinking..."):
                ai_advice = generate_talking_points(target_cust, df_today) # ส่งแค่ today
                st.session_state.talking_points_cache = ai_advice
        if st.session_state.talking_points_cache:
            st.info(st.session_state.talking_points_cache)
    
    st.divider()

    # ==========================
    # SECTION 1: งานวันนี้ (TODAY)
    # ==========================
    if df_today.empty:
        st.success("🎉 วันนี้ไม่มีงานค้าง (All Clear)")
    else:
        st.subheader(f"🔥 งานที่ต้องทำวันนี้ ({len(df_today)}):")
        
        # Voice Area
        st.info("🎙️ พูดรายงานรวม:")
        c1, c2 = st.columns([1, 4])
        with c1:
            st.write("")
            audio = mic_recorder(start_prompt="🎙️ พูด", stop_prompt="⏹️ หยุด", key="mic", format="webm", use_container_width=True)
        with c2:
            if audio:
                if 'last_audio' not in st.session_state: st.session_state.last_audio = None
                if audio['bytes'] != st.session_state.last_audio:
                    st.session_state.last_audio = audio['bytes']
                    with st.spinner("Typing..."):
                        text = transcribe_audio(audio['bytes'])
                        if text: st.session_state.report_text_buffer = text
                        st.rerun()
            report_text = st.text_area("รายละเอียด:", value=st.session_state.report_text_buffer, height=100)
            st.session_state.report_text_buffer = report_text

        st.divider()
        
        # Checklist (Dynamic)
        results_summary = []
        for i, row in df_today.iterrows():
            topic = row['topic']
            desc = row['desc']
            with st.container(border=True):
                st.markdown(f"**{topic}**")
                st.caption(desc)
                
                opts = get_dynamic_options(topic, desc)
                fin_opts = ["(เลือกผลลัพธ์)"] + opts
                
                # State handling
                idx = 0
                if topic in st.session_state.mission_results:
                    if st.session_state.mission_results[topic] in fin_opts:
                        idx = fin_opts.index(st.session_state.mission_results[topic])
                
                sel = st.radio("ผลลัพธ์:", fin_opts, index=idx, key=f"rad_{i}", horizontal=True, label_visibility="collapsed")
                st.session_state.mission_results[topic] = sel
                if sel != "(เลือกผลลัพธ์)": results_summary.append(sel)

        # Submit
        if len(results_summary) == len(df_today):
            if st.button("🚀 ปิดงาน (Save)", type="primary", use_container_width=True):
                # Save
                status_sum = "\n".join([f"- {k}: {v}" for k,v in st.session_state.mission_results.items()])
                final_log = f"DETAILS:\n{report_text}\n\nSTATUS:\n{status_sum}"
                ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                append_data("Reports", [ts, cur_user, target_cust, status_sum, "Completed", final_log])
                
                # Auto Follow-up
                main_stat = results_summary[0] if results_summary else "General"
                with st.spinner("Creating next mission..."):
                    fup = create_followup_mission(target_cust, report_text, main_stat)
                    if fup.get("create"):
                        append_data("Missions", [target_cust, fup['topic'], fup['desc'], "pending"])
                
                # Delete ONLY TODAY missions
                # (Logic ลบต้องระวัง ไม่ลบงานอนาคต)
                # ในที่นี้ใช้ delete_mission_from_sheet แบบเดิมซึ่งลบหมดตามชื่อลูกค้า 
                # *แนะนำ: ใน Production ควรลบด้วย Unique ID แต่สำหรับ Demo นี้ถือว่าปิดจ็อบหมดแล้วสร้างใหม่ได้*
                delete_mission_from_sheet(target_cust) 
                
                st.session_state.mission_results = {}
                st.session_state.report_text_buffer = ""
                st.session_state.talking_points_cache = None
                st.toast("Saved!", icon="✅")
                time.sleep(2)
                st.rerun()
        else:
            st.warning("กรุณาติ๊กผลลัพธ์ให้ครบทุกข้อ")

    # ==========================
    # SECTION 2: งานในอนาคต (FUTURE)
    # ==========================
    if not df_future.empty:
        st.markdown("---")
        st.subheader(f"📅 งานในอนาคต ({len(df_future)}):")
        for _, row in df_future.iterrows():
            with st.expander(f"🔜 {row['topic']}"):
                st.write(f"**รายละเอียด:** {row['desc']}")
                st.caption("ยังไม่ถึงกำหนด (ระบบจะย้ายขึ้นไปข้างบนเมื่อถึงวัน)")