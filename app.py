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

st.set_page_config(page_title="RC Sales AI (Report Text)", layout="wide", page_icon="📝")

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

# ... จบฟังก์ชัน delete_mission_from_sheet ...

# ==========================================
# [เพิ่มใหม่] ฟังก์ชัน AI Talking Points (Groq)
# ==========================================
def generate_talking_points(customer_name, mission_df):
    try:
        if "GROQ_API_KEY" not in st.secrets:
            return "⚠️ กรุณาใส่ GROQ_API_KEY ใน Secrets"

        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        
        # รวบรวมโจทย์ (Mission)
        if not mission_df.empty:
            tasks_list = [f"- {row['topic']}: {row['desc']}" for _, row in mission_df.iterrows()]
            tasks_text = "\n".join(tasks_list)
        else:
            # ถ้าไม่มีโจทย์ ให้ AI คิดเรื่องทั่วไป
            tasks_text = "ไม่มีโจทย์พิเศษ (เน้นสร้างความสัมพันธ์และอัปเดตสถานการณ์ทั่วไป)"

        # --- แก้ Prompt ใหม่ ให้ฉลาดและตรงประเด็นขึ้น ---
        prompt = f"""
        บทบาทของคุณ: คุณคือ "ผู้ช่วยส่วนตัวของเซลล์มืออาชีพ" (Professional Sales Assistant) ที่เก่งเรื่องศิลปะการพูดและการเข้าสังคม
        
        สถานการณ์: เซลล์กำลังจะไปเยี่ยมลูกค้าชื่อ: "{customer_name}"
        
        โจทย์สำคัญ (Mission) วันนี้คือ:
        {tasks_text}
        
        คำสั่ง:
        ช่วยคิดบทพูดให้เซลล์ โดยต้อง **"ปรับน้ำเสียง (Tone)" ให้เข้ากับโจทย์**:
        - ถ้าโจทย์คือเรื่องซีเรียส (ราคา, คู่แข่ง, สัญญา): ให้ใช้โทนจริงจัง มืออาชีพ น่าเชื่อถือ
        - ถ้าโจทย์คือเรื่องความสัมพันธ์ (เชิญงานปีใหม่, กินข้าว, ตีกอล์ฟ): ให้ใช้โทนอบอุ่น เป็นกันเอง ให้เกียรติ และเชื้อเชิญ
        
        สิ่งที่ต้องการ (Output):
        1. 🧊 Ice Breaker (1 ประโยค): ประโยคเปิดบทสนทนาที่เข้ากับสถานการณ์
        2. 🎯 Key Talking Points (3 ข้อ): 
           - ถ้าเป็นงานเลี้ยง: ให้เน้นพูดถึงความสำคัญของลูกค้า อยากขอบคุณที่สนับสนุนกันมา และรายละเอียดงาน
           - ถ้าเป็นงานขาย: ให้เน้นคำถามจิตวิทยาเพื่อล้วงข้อมูล
        
        **ห้ามพูดเรื่องที่ไม่เกี่ยวกับโจทย์ และห้ามใช้ภาษาลิเกเกินไป ขอภาษาคนทำงานคุยกัน**
        """
        
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=600
        )
        return completion.choices[0].message.content
    except Exception as e:
        return f"AI Error: {str(e)}"
    

# ==========================================
# 2. VOICE FUNCTION (Debug Mode)
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
        st.error(f"Voice Error: {e}")
        return None

# ==========================================
# 3. LOAD DATA
# ==========================================
try:
    df_assignments = get_data("Assignments")
    df_missions = get_data("Missions")
except:
    st.stop()

# ใช้ session_state เพื่อเก็บข้อความรายงาน
if 'report_text_buffer' not in st.session_state:
    st.session_state.report_text_buffer = ""
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
    
    # 1. Login & Filter Customer List
    sales_list = df_assignments['Sales_Rep'].unique() if not df_assignments.empty else []
    current_user = st.selectbox("👤 Login:", sales_list)
    
    my_custs = []
    if not df_assignments.empty and current_user:
        my_custs = df_assignments[df_assignments['Sales_Rep'] == current_user]['Customer'].unique()
    
    st.divider()
    
    # 2. เลือกลูกค้า (ประกาศครั้งเดียวพอ)
    target_cust = st.selectbox("🏢 เลือกลูกค้าที่เข้าเยี่ยม:", my_custs)
    
    # 3. ดึง Mission มาเตรียมไว้ (ประกาศครั้งเดียวตรงนี้ ใช้ได้ยาวจนจบบรรทัดล่าง)
    my_missions = pd.DataFrame()
    if not df_missions.empty and 'Customer' in df_missions.columns:
        my_missions = df_missions[df_missions['Customer'] == target_cust]

    # ==========================================
    # [ส่วนแทรก] AI Talking Points
    # ==========================================
    with st.expander("✨ ให้ AI ช่วยคิดบทพูด (Talking Points)", expanded=False):
        if st.button("💡 กดเพื่อให้ AI วิเคราะห์โจทย์"):
            with st.spinner("AI กำลังวางแผนการขายให้คุณ..."):
                ai_advice = generate_talking_points(target_cust, my_missions)
                st.markdown(ai_advice)
    
    st.divider()

    # 4. Logic รีเซ็ตกล่องข้อความเมื่อเปลี่ยนลูกค้า
    if 'last_cust' not in st.session_state:
        st.session_state.last_cust = target_cust
    if st.session_state.last_cust != target_cust:
        st.session_state.report_text_buffer = ""
        st.session_state.last_cust = target_cust

    # 5. แสดงผล Checklist (ใช้ตัวแปร my_missions ที่ประกาศไว้ข้อ 3 ได้เลย)
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
        
        # --- VOICE RECORDER ---
        st.write("📝 **รายละเอียดการเข้าพบ (พูดแล้วข้อความจะขึ้นด้านล่าง):**")
        
        col_rec, col_area = st.columns([1, 3])
        
        with col_rec:
            st.write("") 
            st.write("")
            audio = mic_recorder(
                start_prompt="🎙️ กดเพื่อพูด",
                stop_prompt="⏹️ หยุด (ส่ง)",
                just_once=True,
                use_container_width=True,
                format="webm",
                key="recorder"
            )
        
        with col_area:
            if audio:
                with st.spinner("กำลังแปลงเสียง..."):
                    text = transcribe_audio(audio['bytes'])
                    if text:
                        if st.session_state.report_text_buffer:
                            st.session_state.report_text_buffer += " " + text
                        else:
                            st.session_state.report_text_buffer = text
                        
                        if completed_count == 0:
                             checklist_status.add(my_missions.iloc[0]['topic'])
                        else:
                             for _, r in my_missions.iterrows(): checklist_status.add(r['topic'])
                        st.session_state.sales_checklist[target_cust] = checklist_status
                        st.rerun()

            final_report = st.text_area(
                "แก้ไขข้อความรายงานได้ที่นี่:",
                value=st.session_state.report_text_buffer,
                height=100
            )
            st.session_state.report_text_buffer = final_report

        # --- SUBMIT BUTTON ---
        st.divider()
        if completed_count < len(my_missions):
            st.warning(f"เหลืออีก {len(my_missions) - completed_count} ข้อ")
        else:
            st.success("✅ ข้อมูลครบถ้วน!")
            if st.button("🚀 ปิดงาน (Save to Cloud)", type="primary"):
                
                topics_str = ", ".join(my_missions['topic'].tolist())
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                report_row = [
                    timestamp, 
                    current_user, 
                    target_cust, 
                    topics_str, 
                    "Completed", 
                    final_report
                ]
                
                append_data("Reports", report_row)
                delete_mission_from_sheet(target_cust)
                
                if target_cust in st.session_state.sales_checklist:
                    del st.session_state.sales_checklist[target_cust]
                st.session_state.report_text_buffer = "" 
                
                st.toast("บันทึกเรียบร้อย!", icon="☁️")
                time.sleep(2)
                st.rerun()