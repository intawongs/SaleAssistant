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

# 3.2 ตรวจการบ้าน (Smart Auditor - ยืดหยุ่น)
# ==========================================
# 3.2 ฟังก์ชันตรวจการบ้าน (Smart Auditor V.4 - ฉลาดและยืดหยุ่นที่สุด)
# ==========================================
def validate_mission_compliance(topic, desc, report_text):
    try:
        if "GROQ_API_KEY" not in st.secrets:
            return "⚠️ No Key", "gray"

        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        
        prompt = f"""
        Role: คุณคือ "ผู้ตรวจสอบข้อมูล" (Auditor) ที่มีวิจารณญาณ
        Task: ตรวจสอบว่า "รายงาน" ตอบโจทย์ "ภารกิจ" หรือไม่
        
        ---
        ภารกิจ: {topic} ({desc})
        รายงาน: "{report_text}"
        ---
        
        ⚠️ กฎเหล็ก (Strict Rules for Passing):
        1. **ห้ามเช็คว่าเซลล์ทวนคำถามไหม:** ห้ามปรับตกเพียงเพราะรายงานไม่ได้พูดชื่อภารกิจซ้ำ (เช่น ถาม "ออเดอร์" ตอบแค่ "ได้เดือนหน้า" --> ให้ถือว่า **PASS**)
        2. **Timeline = PASS:** ถ้ามีการระบุช่วงเวลาที่จะรู้ผล (เช่น มกรา, ปลายเดือน, ปีหน้า) ให้ถือว่าเซลล์ทำงานแล้วและได้คำตอบเป็น Pending Date --> **PASS**
        3. **Context:** ให้เหมาว่าทุกคำพูดในรายงาน กำลังพูดถึงภารกิจนี้อยู่
        
        Output Format (ตอบบรรทัดเดียว):
        [PASS/FAIL]: [เหตุผลสั้นๆ]
        """
        
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0, # ใช้ 0 เพื่อให้ทำตามกฎเป๊ะๆ
            max_tokens=100
        )
        result = completion.choices[0].message.content
        
        if "PASS" in result: return result, "green"
        else: return result, "red"
            
    except Exception as e:
        return f"Error: {e}", "gray"

# 3.3 สร้างงานติดตามผลอัตโนมัติ (Auto-Followup)
def create_followup_mission(customer, report_text):
    try:
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        today = datetime.datetime.now().strftime("%d/%m/%Y")
        
        prompt = f"""
        Role: ระบบ CRM อัตโนมัติ
        Date: {today}
        Report: "{report_text}"
        
        คำสั่ง: สร้าง "ภารกิจติดตามผล (Next Mission)" โดยยึดหลักการ **"Always Follow-up"**:
        
        1. **กรณีมีกำหนดการชัดเจน (Specific Date):**
           - เช่น "ขอคิดดู 2 อาทิตย์", "สรุปงบเดือนหน้า", "โทรมาศุกร์นี้"
           - ให้สร้างงานล่วงหน้าก่อนถึงกำหนดนั้นเล็กน้อย
           - Topic: "ติดตามผล: [เรื่องเดิม]"
           
        2. **กรณีอื่นๆ ทั้งหมด (General / Not Interested):**
           - เช่น "ยังไม่สนใจ", "ของเต็ม", "เงียบ", "ปฏิเสธ", หรือ "ไม่ได้ระบุวัน"
           - **บังคับสร้างงานเดือนถัดไปทันที (Monthly Touchbase)** ห้ามปล่อยว่าง
           - Topic: "Monthly Visit"
           - Desc: "รักษาความสัมพันธ์ต่อเนื่อง (อ้างอิงรายงานเก่า: ...)"
           
        Output JSON:
        {{
            "create": true,
            "topic": "...",
            "desc": "...",
            "status": "pending"
        }}
        """
        
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            response_format={"type": "json_object"}
        )
        
        return json.loads(completion.choices[0].message.content)
        
    except Exception as e:
        return {
            "create": True,
            "topic": "Monthly Visit (System Auto)",
            "desc": "ระบบสร้างให้อัตโนมัติเพื่อความต่อเนื่อง",
            "status": "pending"
        }

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
    st.session_state.talking_points_cache = None
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
    
    # Logic รีเซ็ต
    if 'last_cust' not in st.session_state:
        st.session_state.last_cust = target_cust
    if st.session_state.last_cust != target_cust:
        st.session_state.report_text_buffer = ""
        st.session_state.sales_checklist = set()
        st.session_state.audit_results = {}
        st.session_state.talking_points_cache = None
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
        
        # === ส่วนอัดเสียง & ประมวลผลอัตโนมัติ ===
        col_mic, col_text = st.columns([1, 4])
        with col_mic:
            st.write("")
            # key ต้องไม่ซ้ำ
            audio = mic_recorder(start_prompt="🎙️ พูด", stop_prompt="⏹️ หยุด", key="main_mic_recorder", format="webm", use_container_width=True)
        
        with col_text:
            # Logic เมื่อพูดจบ
            if audio:
                if 'last_processed_audio' not in st.session_state:
                    st.session_state.last_processed_audio = None
                
                if audio['bytes'] != st.session_state.last_processed_audio:
                    st.session_state.last_processed_audio = audio['bytes']
                    
                    with st.spinner("กำลังแปลงเสียง และตรวจคำตอบ..."):
                        text = transcribe_audio(audio['bytes'])
                        if text:
                            # [Overwite] ทับข้อความเดิมทันที
                            st.session_state.report_text_buffer = text
                            
                            # Auto-Audit Logic
                            current_report = st.session_state.report_text_buffer
                            checklist_status = st.session_state.sales_checklist
                            
                            for index, row in my_missions.iterrows():
                                topic = row['topic']
                                desc = row['desc']
                                
                                # [NEW] Bypass Logic: ถ้าเป็นงานทั่วไป ให้ผ่านเลยไม่ต้องตรวจ
                                is_general_task = any(kw in topic.lower() for kw in ["monthly", "visit", "contact", "เยี่ยม", "ทั่วไป", "ติดตาม"])
                                
                                if is_general_task:
                                    result, color = "✅ บันทึกข้อมูลเรียบร้อย (General Visit)", "green"
                                else:
                                    # ถ้างานเจาะจง ให้ AI Auditor ตรวจ
                                    result, color = validate_mission_compliance(topic, desc, current_report)
                                
                                st.session_state.audit_results[topic] = (result, color)
                                
                                if color == "green":
                                    checklist_status.add(topic)
                                else:
                                    if topic in checklist_status:
                                        checklist_status.remove(topic)
                            
                            st.session_state.sales_checklist = checklist_status
                            st.rerun()
            
            # กล่องข้อความ
            main_report_text = st.text_area("📝 รายงานผลรวม:", value=st.session_state.report_text_buffer, height=100)
            st.session_state.report_text_buffer = main_report_text
            
            # ปุ่มตรวจมือ
            if st.button("🔄 ตรวจสอบข้อความที่พิมพ์แก้ใหม่"):
                with st.spinner("AI กำลังตรวจใหม่..."):
                    checklist_status = st.session_state.sales_checklist
                    for index, row in my_missions.iterrows():
                        topic = row['topic']
                        desc = row['desc']
                        
                        # Bypass Logic เดิม
                        is_general_task = any(kw in topic.lower() for kw in ["monthly", "visit", "contact", "เยี่ยม", "ทั่วไป", "ติดตาม"])
                        
                        if is_general_task:
                            result, color = "✅ บันทึกข้อมูลเรียบร้อย (General Visit)", "green"
                        else:
                            result, color = validate_mission_compliance(topic, desc, main_report_text)
                            
                        st.session_state.audit_results[topic] = (result, color)
                        if color == "green":
                            checklist_status.add(topic)
                        elif topic in checklist_status:
                            checklist_status.remove(topic)
                    st.session_state.sales_checklist = checklist_status
                    st.rerun()

        st.divider()

        # === แสดงผลการตรวจ (Checklist + Guideline) ===
        checklist_status = st.session_state.sales_checklist
        
        for index, row in my_missions.iterrows():
            topic = row['topic']
            desc = row['desc']
            is_done = topic in checklist_status
            
            icon = "✅" if is_done else "🔴"
            
            # เช็คงานทั่วไป
            is_general_task = any(kw in topic.lower() for kw in ["monthly", "visit", "contact", "เยี่ยม", "ทั่วไป", "ติดตาม"])
            
            with st.expander(f"{icon} **{topic}**: {desc}", expanded=not is_done):
                
                # [Guideline] โชว์เฉพาะงานที่ต้องตรวจ
                if not is_general_task:
                    st.markdown("""
                    <div style="background-color: #f0f2f6; padding: 10px; border-radius: 5px; margin-bottom: 10px; font-size: 0.85em; color: #31333F;">
                        💡 <b>Guideline:</b> ระบุข้อมูล/ตัวเลข หรือ Timeline วันที่จะรู้ผล
                    </div>
                    """, unsafe_allow_html=True)

                if topic in st.session_state.audit_results:
                    res_text, res_color = st.session_state.audit_results[topic]
                    display_text = res_text.replace("PASS:", "").replace("FAIL:", "").strip()
                    
                    if res_color == "green":
                        st.success(f"ผลตรวจ: {display_text}")
                    else:
                        st.error(f"ผลตรวจ: {display_text}")
                else:
                    if is_general_task:
                        st.info("รอรับข้อมูล (ไม่ต้องตรวจสอบ)")
                    else:
                        st.info("รอข้อมูล... (AI จะช่วยตรวจสอบ)")

        # === Submit ===
        completed_count = len(checklist_status)
        total_count = len(my_missions)
        
        st.write(f"---")
        col_status, col_btn = st.columns([3, 1])
        with col_status:
            st.caption(f"ความคืบหน้า: {completed_count}/{total_count}")
            st.progress(completed_count / total_count if total_count > 0 else 0)
        
        with col_btn:
            if completed_count == total_count:
                if st.button("🚀 ปิดงาน", type="primary", use_container_width=True):
                    topics_str = ", ".join(checklist_status)
                    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    report_row = [timestamp, current_user, target_cust, topics_str, "Completed", main_report_text]
                    
                    append_data("Reports", report_row)
                    delete_mission_from_sheet(target_cust)
                    
                    # Auto-Followup
                    with st.spinner("AI กำลังสร้างงานติดตามผล..."):
                        followup = create_followup_mission(target_cust, main_report_text)
                        if followup.get("create"):
                            append_data("Missions", [target_cust, followup['topic'], followup['desc'], "pending"])
                            st.toast(f"สร้างงานใหม่: {followup['topic']}", icon="📅")
                    
                    if target_cust in st.session_state.sales_checklist:
                        del st.session_state.sales_checklist[target_cust]
                    st.session_state.report_text_buffer = "" 
                    st.session_state.audit_results = {}
                    st.session_state.talking_points_cache = None
                    
                    st.toast("บันทึกเรียบร้อย!", icon="☁️")
                    time.sleep(2)
                    st.rerun()
            else:
                st.button("🔒 ปิดงาน", disabled=True, use_container_width=True, help="ต้องผ่านทุกข้อก่อน")