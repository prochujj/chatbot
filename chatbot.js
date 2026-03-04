// ======================================= 
// ไฟล์: chatbot.js (อัปเกรดให้เชื่อมกับ Python RAG ของเรา)
// ======================================= 

// 1. โครงสร้าง HTML ของแชทบอทแบบฝัง (Widget)
const chatbotTemplate = `
    <button class="chat-btn" onclick="toggleChat()">💬</button>
    <div class="chat-window" id="chatWindow">
        <div class="chat-header">ผู้ช่วย AI ภาคไอโอที</div>
        <div class="chat-body" id="chatBody">
            <div class="bot-msg">สวัสดีครับ! สอบถามข้อมูลเกี่ยวกับภาควิชาได้เลยครับ</div>
        </div>
        <div class="chat-input-area">
            <input type="text" id="userInput" placeholder="พิมพ์ข้อความ..." onkeypress="if(event.key === 'Enter') sendChatMessage()">
            <button onclick="sendChatMessage()">ส่ง</button>
        </div>
    </div>
`;

// เสกโครงสร้างแชทลงไปในหน้าเว็บที่เรียกใช้สคริปต์นี้
document.addEventListener("DOMContentLoaded", () => {
    document.body.insertAdjacentHTML("beforeend", chatbotTemplate);
});

// 2. ฟังก์ชันเปิด/ปิดกล่องแชท
function toggleChat() {
    let chat = document.getElementById("chatWindow");
    chat.style.display = (chat.style.display === "none" || chat.style.display === "") ? "flex" : "none";
}

// 3. 🔴 ฟังก์ชันส่งข้อความ (เปลี่ยนไปหาเซิร์ฟเวอร์ Python)
async function sendChatMessage() {
    let inputField = document.getElementById("userInput");
    let message = inputField.value.trim();
    if (message === "") return;

    let chatBody = document.getElementById("chatBody");

    // โชว์ข้อความผู้ใช้
    chatBody.innerHTML += `<div class="user-msg">${message}</div>`;
    inputField.value = "";
    chatBody.scrollTop = chatBody.scrollHeight;

    // โชว์สถานะกำลังค้นหาข้อมูล
    chatBody.innerHTML += `<div class="bot-msg" id="loadingMsg">กำลังให้ AI ค้นหาข้อมูล... ⏳</div>`;
    chatBody.scrollTop = chatBody.scrollHeight;

    try {
        // 🔴 ยิงคำถามไปที่ Python Backend (Ollama + RAG) ของเรา
        const response = await fetch("http://localhost:5000/ask", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ question: message })
        });

        const data = await response.json();
        let loadingElement = document.getElementById("loadingMsg");

        // จัดการกรณีเซิร์ฟเวอร์ Python ส่ง Error กลับมา
        if (!response.ok || data.error) {
            loadingElement.innerText = "เกิดข้อผิดพลาดจากฝั่งเซิร์ฟเวอร์: " + (data.error || "Unknown Error");
            loadingElement.removeAttribute("id");
            return;
        }

        // นำคำตอบที่ประมวลผลแล้วมาแสดงผล
        if (data.answer) {
            loadingElement.innerHTML = data.answer.replace(/\n/g, "<br>");
            loadingElement.removeAttribute("id"); 
        }

    } catch (error) {
        // จัดการกรณีไม่ได้เปิดเซิร์ฟเวอร์ Python
        let loadingElement = document.getElementById("loadingMsg");
        loadingElement.innerText = "หาเซิร์ฟเวอร์ไม่เจอครับ ลืมรันไฟล์ Python (app.py) หรือเปล่า?";
        loadingElement.removeAttribute("id");
    }
    chatBody.scrollTop = chatBody.scrollHeight;
}