import json
import os
from unicodedata import category
from urllib import response
import ollama
from flask import Flask, request, jsonify
from flask_cors import CORS
import numpy as np
from sentence_transformers import SentenceTransformer 
from sklearn.metrics.pairwise import cosine_similarity 
import mysql.connector
import uuid

app = Flask(__name__)
CORS(app)
DB_HOST = "localhost"
DB_USER = "root"      # ใส่ Username ของ phpMyAdmin
DB_PASS = ""          # ใส่ Password (ถ้าไม่มีปล่อยว่าง)
DB_NAME = "chatbot"

CLEAN_DATA_DIR = r'C:\mobile_app\chatbot\clean_data'
GEMINI_DATA_FILE = r'C:\mobile_app\chatbot\datasetgemini\train_iot_premium.json'

def generate_session_id():
    return str(uuid.uuid4())

def init_db():
    """สร้างตารางใน phpMyAdmin อัตโนมัติถ้ายังไม่มี"""
    print("🛠️ กำลังตรวจสอบและสร้างฐานข้อมูล MySQL...")
    try:
        # ต่อแบบไม่ระบุ DB เพื่อสร้าง DB ก่อน
        conn = mysql.connector.connect(host=DB_HOST, user=DB_USER, password=DB_PASS)
        c = conn.cursor()
        c.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        conn.close()

        # ต่อแบบระบุ DB เพื่อสร้างตาราง
        conn = mysql.connector.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS chat_history (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        session_id VARCHAR(255),
                        role VARCHAR(50),
                        content TEXT,
                        category VARCHAR(100),
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                     )''')
        conn.commit()
        conn.close()
        print("✅ ฐานข้อมูลพร้อมใช้งาน!")
    except Exception as e:
        print(f"❌ เชื่อมต่อ MySQL ไม่ได้ (เปิด XAMPP หรือยังครับ?): {e}")

def categorize_question(user_message):
    """แยกหมวดหมู่คำถามด้วย Keyword"""
    categories = {
        "หลักสูตร": ["หลักสูตร", "เรียนกี่ปี", "สาขา", "วุฒิ", "ปริญญา"],
        "อาจารย์": ["อาจารย์", "อ.", "ดร.", "หัวหน้าภาค", "ผู้สอน"],
        "รายวิชา": ["วิชา", "เรียนอะไร", "หน่วยกิต", "แคลคูลัส", "ฟิสิกส์", "โปรแกรม"],
        "การรับสมัคร": ["tcas", "รับสมัคร", "โควตา", "สอบเข้า", "พอร์ต"],
        "ค่าใช้จ่าย": ["ค่าเทอม", "กู้", "กยศ", "ทุน", "จ่าย"]
    }
    text = user_message.lower()
    for cat_name, keywords in categories.items():
        for kw in keywords:
            if kw in text:
                return cat_name
    return "ทั่วไป/ไม่ทราบ"

def save_message_mysql(session_id, role, source, content, category="-"):
    try:
        conn = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME
        )
        c = conn.cursor()
        c.execute("""
            INSERT INTO chat_history 
            (session_id, role, source, content, category) 
            VALUES (%s, %s, %s, %s, %s)
        """, (session_id, role, source, content, category))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ บันทึก DB พลาด: {e}")

def get_chat_history(session_id, limit=6):
    """ดึงประวัติการคุยล่าสุดมาทบทวน"""
    try:
        conn = mysql.connector.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME)
        c = conn.cursor()
        c.execute("SELECT role, content FROM chat_history WHERE session_id=%s ORDER BY id DESC LIMIT %s", (session_id, limit))
        rows = c.fetchall()
        conn.close()
        
        # กลับด้านให้เรียงจากอดีต -> ปัจจุบัน
        return [{"role": row[0], "content": row[1]} for row in reversed(rows)]
    except:
        return []

# เตรียมสมองและฝังความจำ (พร้อมระบบกรองข้อมูลซ้ำ)

print("กำลังโหลดโมเดล Vector (ครั้งแรกอาจใช้เวลาโหลดสักครู่)... ⏳")
embedder = SentenceTransformer('intfloat/multilingual-e5-base') 

knowledge_base = []
seen_contents = set() # 🔴 1. สร้างสมุดจดจำข้อความที่เคยอ่านแล้ว
knowledge_vectors = None

def add_to_knowledge(content):
    content = content.strip()
    if content and content not in seen_contents:
        seen_contents.add(content)
        knowledge_base.append(content)

if os.path.exists(CLEAN_DATA_DIR):
    print(f"กำลังค้นหาไฟล์ข้อมูลในโฟลเดอร์: {CLEAN_DATA_DIR}")
    for filename in os.listdir(CLEAN_DATA_DIR):
        if filename.endswith('.json'):
            file_path = os.path.join(CLEAN_DATA_DIR, filename)
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
                for item in data:
                    content = item.get('output', '').strip()
                    
                    # 🔴 2. เช็คว่า "ข้อความนี้เคยเอาใส่ตะกร้าไปหรือยัง?"
                    if content and content not in seen_contents:
                        seen_contents.add(content) # จดบันทึกไว้ว่าอ่านแล้ว
                        knowledge_base.append(content) # เอาใส่ตะกร้าของจริง
                        
            print(f"อ่านไฟล์ {filename} สำเร็จ!")

if os.path.exists(GEMINI_DATA_FILE):
    print(f"\n📄 2. กำลังโหลดไฟล์ข้อมูลสังเคราะห์จาก Gemini...")
    with open(GEMINI_DATA_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    add_to_knowledge(item.get('output', ''))
    print("  ✅ โหลดไฟล์ Gemini สำเร็จ!")
else:
    print(f"  ❌ ไม่พบไฟล์ Gemini: {GEMINI_DATA_FILE}")
if len(knowledge_base) > 0:
    print(f"\n🎉 รวบรวมข้อมูลเสร็จสิ้น! ได้ความรู้(ที่ไม่ซ้ำกัน) รวม {len(knowledge_base)} ก้อน")
    print("🧠 กำลังแปลงข้อมูลทั้งหมด เป็นพิกัด Vector... ⏳")
    knowledge_vectors = embedder.encode(knowledge_base)
    print("🚀 ระบบ Vector Search อัปเกรดสมอง 2 แหล่งเสร็จสิ้น! พร้อมทำงาน! ✅\n")
else:
    print("\n❌ ไม่พบข้อมูลจากทั้ง 2 แหล่งเลยครับ โปรดตรวจสอบ Path อีกครั้ง")




def expand_query_with_llm(user_query):
    print(f"\n🧠 กำลังให้ AI ช่วยขยายคำถาม: '{user_query}'...")
    
    # 🔴 ปรับ Prompt เป็นการเขียนประโยค และตั้งกฎห้ามใช้คำว่า "หลักสูตร" โดดๆ
    prompt = f"""หน้าที่ของคุณคือวิเคราะห์คำถามของผู้ใช้ และเขียนใหม่ให้เป็น 'ประโยคค้นหาที่สมบูรณ์' (Search Sentence)
    กฎเหล็ก:
    1. เขียนเป็นประโยคยาวๆ ที่อ่านเป็นธรรมชาติ ห้ามคั่นด้วยลูกน้ำ (,) เด็ดขาด
    2. ห้ามใช้คำว่า "หลักสูตร" โดดๆ ให้ใช้คำว่า "เนื้อหาการเรียน" หรือ "รายวิชา" แทน (เพื่อป้องกันการไปค้นเจอตำแหน่ง 'อาจารย์ประจำหลักสูตร')
    3. ห้ามเติมคำศัพท์เฉพาะทางเทคโนโลยีที่ผู้ใช้ไม่ได้ถาม
    4. [กฎเหล็กสำคัญ] ถ้าผู้ใช้ถามเรื่อง 'การรับสมัคร', 'TCAS', 'เกณฑ์คะแนน', 'โควตา' หรือ 'จำนวนรับ' ให้คุณตอบแค่ประโยคนี้เท่านั้น: 
    "'ขอให้ติดตามข่าวสารและประกาศรับสมัครล่าสุดได้ที่เว็บไซต์ของภาควิชาฯ (www.iote.kmitl.ac.th) ครับ' "
    "โดยห้ามอธิบายเรื่องเกณฑ์คะแนนหรือจำนวนคนรับเด็ดขาด เพื่อป้องกันความผิดพลาดของข้อมูล"
    
    ตัวอย่างที่ 1:
    คำถาม: ภาคไอโอทีอยู่ที่ไหน
    ประโยคค้นหา: ข้อมูลสถานที่ตั้ง อาคารที่อยู่ และช่องทางการติดต่อของภาควิชาวิศวกรรมระบบไอโอทีและสารสนเทศ
    
    ตัวอย่างที่ 2:
    คำถาม: สาขานี้เรียนเกี่ยวกับอะไร
    ประโยคค้นหา: ข้อมูลเนื้อหาการเรียนการสอน รายวิชาที่เรียนเกี่ยวกับการพัฒนาซอฟต์แวร์ ฮาร์ดแวร์ และระบบไอโอที
    
    คำถาม: {user_query}
    ประโยคค้นหา:"""
    
    try:
        response = ollama.chat(model='local-typhoon2:latest', messages=[{'role': 'user', 'content': prompt}])
        expanded = response['message']['content'].strip()
        
        # ลบคำนำหน้าเผื่อ AI พ่นติดมาด้วย
        if expanded.startswith("ประโยคค้นหา:"):
            expanded = expanded.replace("ประโยคค้นหา:", "").strip()
            
        print(f"✨ ประโยคที่ใช้ค้นหาจริง: {expanded}")
        return expanded
    except:
        return user_query

# =========================
# 🔒 SYSTEM RULES (Hard Rule)
# =========================

ADMISSION_KEYWORDS = [
    "รับสมัคร", "tcas", "เกณฑ์", "คะแนน", "โควตา", "จำนวนรับ", "สมัครเรียน"
]

BAD_WORDS = ["ควย", "เฮี้ย", "กู", "เหี้ย", "สัส", "แม่ง", "เสียว"]

FIXED_ADMISSION_REPLY = (
    "ขอให้ติดตามข่าวสารและประกาศรับสมัครล่าสุดได้ที่เว็บไซต์ของภาควิชาฯ "
    "(www.iote.kmitl.ac.th) ครับ"
)

BAD_WORD_REPLY = "ขอโทษครับ ผมเป็นคนดีเกินไปที่จะตอบคำถามแบบนั้นได้"

def is_admission_question(text):
    text = text.lower()
    return any(k in text for k in ADMISSION_KEYWORDS)

def contains_bad_words(text):
    return any(b in text for b in BAD_WORDS)

def get_semantic_knowledge(user_query):
    if not knowledge_base or knowledge_vectors is None:
        return "ไม่พบข้อมูลในระบบ"
    
def build_instruction_rules():
    return (
        "กฎการตอบคำถาม:\n"
        "1. คุณคือผู้ช่วย AI ของภาควิชาวิศวกรรมระบบไอโอทีและสารสนเทศ สจล.\n"
        "2. ใช้ข้อมูลใน 'บริบทข้อมูล' เป็นหลัก และสามารถสรุปหรือวิเคราะห์ต่อได้ "
        "แต่ห้ามสร้างข้อมูลใหม่ที่ไม่มีอยู่ในบริบท\n"
        "3. อธิบายเชื่อมโยงกับบริบทของภาควิชาในเชิงภาพรวมเท่านั้น\n"
        "4. หากบริบทข้อมูลระบุว่าไม่พบข้อมูล ให้ตอบอย่างสุภาพว่า "
        "'ขออภัยครับ ข้อมูลส่วนนี้ยังไม่ได้ระบุไว้ในระบบของผมครับ'\n"
        "5. ห้ามตอบว่า 'นอกเรื่อง' หรือปัดคำถามทิ้ง\n"
        "6. ความยาวคำตอบ 15–50 คำ ถ้าข้อมูลเยอะให้สรุป\n"
        "7. ภาษาธรรมชาติ ผิดเล็กน้อยได้ ไม่ต้องเป็นทางการเกินไป\n"
        "8. ตอบเป็นภาษาไทย ห้ามใช้จุด (.) และห้ามเว้นวรรคแปลก ๆ ระหว่างประโยค\n"
        "9.ห้ามใช้คำสรรพนามลอยๆ (เช่น เขา, อาจารย์ท่านนี้, ความเชี่ยวชาญนี้) ให้ระบุ ชื่อ-นามสกุล หรือชื่อวิชา ลงไปในคำถามและคำตอบทุกครั้ง"
    )
def get_semantic_knowledge(user_query):
    if not knowledge_base or knowledge_vectors is None:
        return "ไม่พบข้อมูลในระบบ"
    # 1. เอาคำถามไปให้ AI ขยายร่างก่อน
    smart_query = expand_query_with_llm(user_query)
    search_text = f"query: {smart_query}"
    
    # 2. แปลงเป็น Vector แล้ววัดมุม
    query_vector = embedder.encode([search_text])
    similarities = cosine_similarity(query_vector, knowledge_vectors)[0]
    
    # 3. หยิบ Top-3 (เกณฑ์คะแนนของ e5 คือ 0.75)
    top_3_indices = np.argsort(similarities)[::-1][:3]
    knowledge_pieces = []
    
    for idx in top_3_indices:
        score = similarities[idx]
        if score > 0.75: 
            knowledge_pieces.append(knowledge_base[idx])
            print(f"🔍 [ดึงข้อมูลคะแนน {score:.2f}]: {knowledge_base[idx][:50]}...")
            
    if knowledge_pieces:
        return "ข้อมูลที่เกี่ยวข้องพบดังนี้:\n" + "\n---\n".join(knowledge_pieces)
    else:
        return "ไม่พบข้อมูลที่เกี่ยวข้องในฐานข้อมูลเลยครับ"

# ==========================================================
#  ระบบแชทบอท (Flask API)
# ==========================================================
@app.route('/ask', methods=['POST'])
def ask_ollama():
    try:
        data = request.json
        user_message = data.get("question", "").strip()
        session_id = data.get("session_id")
        if not session_id:
            session_id = generate_session_id()
           

        # 🔒 Hard rule
        if contains_bad_words(user_message):
            return jsonify({"answer": BAD_WORD_REPLY})

        if is_admission_question(user_message):
            return jsonify({"answer": FIXED_ADMISSION_REPLY})
        
        category = categorize_question(user_message)
        save_message_mysql(
    session_id,
    role="user",
    source="user",
    content=user_message,
    category=category)
        knowledge = get_semantic_knowledge(user_message)
        instruction_rules = build_instruction_rules()

        final_prompt = (
            f"{instruction_rules}\n\n"
            f"บริบทข้อมูล:\n{knowledge}\n\n"
            f"คำถามผู้ใช้: {user_message}\n"
            "คำตอบ:"
        )

        response = ollama.chat(
            model='local-typhoon2:latest',
            messages=[{'role': 'user', 'content': final_prompt}]
        )

        answer = response['message']['content']
        save_message_mysql(
    session_id,
    role="assistant",
    source="ai",
    content=answer,
    category=category
)

        return jsonify({"answer": answer,"session_id": session_id})

    except Exception as e:
        return jsonify({"error": f"เซิร์ฟเวอร์มีปัญหา: {str(e)}"}), 500


if __name__ == '__main__':
    init_db()  # ตรวจสอบและสร้างฐานข้อมูลก่อนเริ่มเซิร์ฟเวอร์
    app.run(port=5000, debug=False)