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

st.set_page_config(page_title="RC Sales AI (Final)", layout="wide", page_icon="🚀")

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
# 2. UTILITIES (Date Parsing Fixed)
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
# [FIXED] ฟังก์ชันแยกแยะวันที่ (รองรับปี 2 หลัก + เวลาไทย)
# ==========================================
def get_task_status_by_date(topic_str):
    try:
        if not isinstance(topic_str, str): return 'today'
        
        # 1. ตั้งค่า "วันนี้" เป็นเวลาไทย (GMT+7)
        tz = datetime.timezone(datetime.timedelta(hours=7))
        today = datetime.datetime.now(tz).date()
        
        # 2. หา Pattern ตัวเลข (รองรับปี 2-4 หลัก)
        # จับรูปแบบ: d/m/yy หรือ d-m-yy (เช่น 27/11/68, 27-11-2568)
        match_digit = re.search(r"(\d{1,2})\s*[\/\-]\s*(\d{1,2})\s*[\/\-]\s*(\d{2,4})", topic_str)
        
        if match_digit:
            d, m, y = map(int, match_digit.groups())
            
            # Logic แปลงปี:
            if y > 2400: 
                # กรณีปีเต็ม พ.ศ. (2568) -> ลบ 543 เป็น ค.ศ.
                y -= 543
            elif y < 100: 
                # กรณีปีย่อ (68) -> ตีว่าเป็น พ.ศ. 25xx -> บวก 1957 เป็น ค.ศ. (2500-543=1957)
                # (เช่น 68 + 1957 = 2025)
                # แต่ถ้าเลขน้อยๆ เช่น 25 อาจจะเป็น ค.ศ. 2025 ก็ได้ (กันเหนียว)
                if y > 40: y += 1957 
                else: y += 2000
            
            try:
                task_date = datetime.date(y, m, d)
                # เทียบกับวันนี้
                return 'future' if task_date > today else 'today'
            except:
                return 'today'

        # 3. หา Pattern ภาษาไทย (เหมือนเดิม)
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

# 3.1 สรุปความ (จับคู่โจทย์ + สั้นกระชับ)
# ==========================================
# 3.1 สรุปความ (Smart Mapping - แสดงเฉพาะสิ่งที่พูด)
# ==========================================
def summarize_voice_report(raw_text, customer_name, mission_df):
    try:
        if "GROQ_API_KEY" not in st.secrets: return raw_text
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        
        # เตรียมรายการโจทย์
        if not mission_df.empty:
            tasks_text = "\n".join([f"- {row['topic']}" for _, row in mission_df.iterrows()])
        else:
            tasks_text = "ไม่มีโจทย์พิเศษ"

        prompt = f"""
        Role: AI สรุปรายงานการขายที่ "เนื้อๆ เน้นๆ"
        Input: "{raw_text}"
        
        Context: เซลล์ไปเยี่ยมลูกค้า "{customer_name}" โดยมีโจทย์ที่ต้องถามคือ:
        {tasks_text}
        
        คำสั่ง (Strict Rules):
        1. **จับคู่:** ถ้าสิ่งที่เซลล์พูด เกี่ยวข้องกับโจทย์ข้อไหน ให้สรุปใส่ข้อนั้น
           Format: "- **[ชื่อโจทย์]**: [เนื้อหาที่เซลล์พูด]"
           
        2. **ตัดทิ้ง (สำคัญมาก):** โจทย์ข้อไหนที่เซลล์ **"ไม่ได้พูดถึง"** ห้ามเขียนออกมาเด็ดขาด! (ห้ามเขียนว่า ไม่มีข้อมูล / ไม่ได้ระบุ)
        
        3. **ส่วนเกิน:** ถ้าสิ่งที่พูด ไม่ตรงกับโจทย์ข้อไหนเลย ให้ใส่ในหัวข้อ "- **ข้อมูลเพิ่มเติม**: ..."
        
        4. **ห้าม** ใส่คำว่า "อื่นๆ: ไม่มีข้อมูล" หรือสรุปจบใดๆ เอาแค่เนื้อหาที่จับคู่ได้เท่านั้น
        """
        
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile", # ใช้ตัวฉลาดสุดเพื่อการจับคู่ที่แม่นยำ
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1, 
            max_tokens=300
        )
        return completion.choices[0].message.content
    except: return raw_text

# 3.2 Auto-Followup (คำนวณวันพรุ่งนี้ + Format หัวข้อเป๊ะๆ)
# ==========================================
# ==========================================
# 3.2 Auto-Followup (Fix: ห้ามเดาวันมั่ว ถ้าไม่เจอ Keyword ให้ไปเดือนหน้า)
# ==========================================
# ==========================================
# 3.2 Auto-Followup (รวมมิตร: ปฏิทิน 7 วัน + กฎเหล็กวันที่ + Format สวย)
# ==========================================
def create_followup_mission(customer, report_text, original_topic):
    try:
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        
        # 1. คำนวณเวลาปัจจุบัน (Thailand)
        tz = datetime.timezone(datetime.timedelta(hours=7))
        now = datetime.datetime.now(tz)
        
        # Helper: แปลงเป็นสตริงไทยแบบย่อ (d/m/yy)
        def to_short_thai_date(dt):
            year_short = str(dt.year + 543)[-2:] 
            return f"{dt.day}/{dt.month}/{year_short}"

        # --- สร้างโพยปฏิทิน 7 วันล่วงหน้า (Calendar Cheat Sheet) ---
        # เพื่อให้ AI เลือกวันในสัปดาห์ได้แม่นยำ 100%
        thai_days = ["จันทร์", "อังคาร", "พุธ", "พฤหัส", "ศุกร์", "เสาร์", "อาทิตย์"]
        calendar_cheat_sheet = ""
        for i in range(1, 8): # ดูล่วงหน้า 7 วัน
            future_date = now + datetime.timedelta(days=i)
            day_name = thai_days[future_date.weekday()]
            d_str = to_short_thai_date(future_date)
            calendar_cheat_sheet += f"- วัน{day_name}หน้า/นี้: {d_str}\n"

        # วันสำคัญอื่นๆ
        today_str = f"วัน{thai_days[now.weekday()]}ที่ {to_short_thai_date(now)}"
        tomorrow_str = to_short_thai_date(now + datetime.timedelta(days=1))
        
        # เดือนหน้า (วันเดิม)
        try:
            next_month_date = now.replace(month=now.month+1)
        except ValueError:
            if now.month == 12: next_month_date = now.replace(year=now.year+1, month=1)
            else: next_month_date = now.replace(month=now.month+1, day=28)
        next_month_str = to_short_thai_date(next_month_date)
        
        prompt = f"""
        Role: ระบบ Scheduler อัจฉริยะ
        
        📅 โพยวันที่ (Reference Dates):
        - วันนี้: {today_str}
        - พรุ่งนี้: {tomorrow_str}
        {calendar_cheat_sheet}
        - เดือนหน้า (Default): {next_month_str}
        
        Input Data:
        - Report: "{report_text}"
        - Topic เดิม: "{original_topic}"
        - Customer: "{customer}"
        
        ภารกิจ: สร้าง Topic งานใหม่ (`create`: true)
        
        🔥 กฎการเลือกวัน (Date Priority):
        1. **เจอคำว่า "พรุ่งนี้"**: -> ใช้ {tomorrow_str}
        2. **เจอ "วันในสัปดาห์"** (เช่น อังคารหน้า, ศุกร์นี้): -> **ห้ามคำนวณเอง** ให้ดูจาก "โพยวันที่" ด้านบนเท่านั้น
        3. **เจอ "วันที่" ชัดเจน** (เช่น 7 ธ.ค.): -> ใช้ d/m/yy (68)
        4. **ไม่เจอเวลาเลย**: -> ใช้ {next_month_str}
        
        🔥 FINAL FORMAT (ต้องตอบรูปแบบนี้เป๊ะๆ):
        "Follow up [วันที่ที่เลือก] {customer} [รายละเอียด]"
        
        ตัวอย่าง: "Follow up {tomorrow_str} {customer} ลูกค้านัดคอนเฟิร์ม"
        
        Output JSON: {{ "create": true, "topic": "...", "desc": "...", "status": "pending" }}
        """
        
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile", 
            messages=[{"role": "user", "content": prompt}], 
            temperature=0.0, 
            response_format={"type": "json_object"}
        )
        return json.loads(completion.choices[0].message.content)
    except:
        return {"create": True, "topic": "Follow up Auto", "desc": "System Auto-Gen", "status": "pending"}
    

# 3.3 AI Coach
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
# 3.2 [FIXED] วิเคราะห์ Sentiment (ตัดคำเวิ่นเว้อทิ้ง)
# ==========================================
# ==========================================
# 3.2 [UPDATED] วิเคราะห์ Sentiment (จูนให้ Positive ง่ายขึ้น)
# ==========================================
def analyze_sentiment(report_text):
    try:
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        
        prompt = f"""
        Role: Sales Analyst ผู้มองโลกในแง่ธุรกิจ
        Task: ให้คะแนน Sentiment จากรายงาน: "{report_text}"
        
        🔥 เกณฑ์การให้คะแนน (Strict Business Criteria):
        
        🟢 Positive (ดี/บวก):
           - **มีออเดอร์** (ไม่ว่าจะสั่งเพิ่ม หรือ สั่งต่อเนื่อง)
           - **ลูกค้ายังใช้อยู่** (Active Customer)
           - สนใจ, นัดวันได้, ตอบรับดี
           - "เหมือนเดิม", "ปกติ" (ในบริบทที่มีออเดอร์ถือว่าดี)
           
        🟡 Neutral (กลางๆ/ทั่วไป):
           - รอตัดสินใจ, รอดูงบ, ยังไม่ฟันธง
           - "ของยังเหลือ" (Stock แน่น เลยยังไม่สั่งเพิ่ม)
           - แจ้งข้อมูลทั่วไป ไม่ได้บอกว่าจะซื้อหรือไม่ซื้อ
           
        🔴 Negative (ลบ/แย่):
           - ปฏิเสธชัดเจน, ไม่สนใจ, เลิกซื้อ
           - บ่นด่า, มีปัญหาคุณภาพสินค้า
           - หันไปใช้คู่แข่ง
        
        Output: เลือก 1 อันเท่านั้น (🟢 Positive / 🟡 Neutral / 🔴 Negative)
        """
        
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0, 
            max_tokens=10
        )
        result = completion.choices[0].message.content.strip()
        
        # Python Cleaning
        if "Positive" in result: return "🟢 Positive"
        if "Negative" in result: return "🔴 Negative"
        return "🟡 Neutral" # Default เป็นกลางไว้ก่อน
        
    except: return "⚪ Unknown"

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
                            # ส่ง df_today ไปให้ AI จับคู่โจทย์
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
                
                # [จุดเรียกใช้] ส่งข้อความสรุป (final_report) ไปให้ AI วิเคราะห์
                sentiment = analyze_sentiment(final_report) 
                
                # [จุดบันทึก] เอาค่า sentiment ใส่ลงไปในลำดับที่ 6
                # Format: [Timestamp, User, Cust, Topics, Status, Sentiment, Summary]
                append_data("Reports", [ts, cur_user, target_cust, topics, "Completed", sentiment, final_report])
                delete_mission_from_sheet(target_cust)
                
                with st.spinner("Creating Next Mission..."):
                    # ส่ง topics เดิมเข้าไปด้วย
                    fup = create_followup_mission(target_cust, final_report, topics)
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