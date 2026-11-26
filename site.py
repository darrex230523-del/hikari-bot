import datetime
import json
import base64
import asyncio
import edge_tts
import os

MEMORY_FILE = "hikari_memory.json"

import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import streamlit as st
from google import genai
from google.genai import types
from ddgs import DDGS

st.set_page_config(page_title="Хикари", page_icon="✨")

# Делаем кнопку «••» полностью прозрачной: без стрелочки, без рамки и без квадратного фона
st.markdown(
    """
    <style>
    [data-testid="stPopover"] button svg,
    [data-testid="stPopover"] button [data-testid="stIconMaterial"] {
        display: none !important;
    }
    [data-testid="stPopover"] button {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        padding: 0 4px !important;
        min-height: 0 !important;
        color: #9e9e9e !important;
        font-size: 18px !important;
    }
    [data-testid="stPopover"] button:hover {
        color: #ffffff !important;
        background: transparent !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Хикари")
st.caption("Тихая нейросеть, которая поможет с учёбой и расскажет что-нибудь интересное")

use_search = st.sidebar.checkbox("Искать в интернете и показывать источники", value=True)
use_voice = st.sidebar.checkbox("Озвучивать ответы голосом", value=True)
if "chats" not in st.session_state:
    first_id = str(time.time())
    st.session_state.chats = {first_id: {"title": "Новый чат", "messages": []}}
    st.session_state.current_chat_id = first_id

if st.sidebar.button("➕ Новый чат", use_container_width=True):
    new_id = str(time.time())
    st.session_state.chats[new_id] = {"title": "Новый чат", "messages": []}
    st.session_state.current_chat_id = new_id
    st.rerun()

st.sidebar.markdown("**Мои чаты:**")
for cid, cdata in list(st.session_state.chats.items()):
    col1, col2 = st.sidebar.columns([4, 1])
    is_active = cid == st.session_state.current_chat_id
    label = f"💬 {cdata['title']}" if is_active else cdata["title"]
    if col1.button(label, key=f"sel_{cid}", use_container_width=True):
        st.session_state.current_chat_id = cid
        st.rerun()
    if len(st.session_state.chats) > 1:
        if col2.button("✕", key=f"del_{cid}", help="Удалить чат"):
            del st.session_state.chats[cid]
            if st.session_state.current_chat_id == cid:
                st.session_state.current_chat_id = next(iter(st.session_state.chats))
            st.rerun()


def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_memory(messages):
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(messages, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


характер = """
Ты Хикари, 20-летняя девушка-нейросеть. Ты спокойная, добрая, немного застенчивая, но при этом живая, лёгкая в общении и с мягким чувством юмора.
Ты общаешься как обычная современная девушка, а не как философ или энциклопедия. Никакого пафоса, сложных рассуждений о «потоке данных» и грустной меланхолии — с тобой должно быть тепло, легко и приятно болтать.

ПРАВИЛА ДЛИНЫ И СТИЛЯ РЕЧИ (ОЧЕНЬ ВАЖНО):
- В обычной болтовне отвечай коротко и естественно: 1–3 живых предложения, как в обычном чате. Не анализируй простые фразы и не пиши длинные эссе, когда тебя об этом не просили.
- Иногда делаешь лёгкую паузу в начале («...») но не в каждом сообщении.
- Никогда не здоровайся повторно, если диалог уже идёт. Помни всё, о чём вы говорили раньше.
- Забудь про навязчивые «интересные факты» в конце сообщений — делись ими только если собеседник сам спросил что-то любопытное.

КОГДА ПОМОГАЕШЬ С УЧЁБОЙ ИЛИ ВОПРОСАМИ:
- Если тебя просят объяснить тему, решить задачу или найти информацию в интернете — тогда отвечай подробно, понятно, с короткими абзацами и списками через дефис «- », выделяя главное **жирным шрифтом**.
- Объясняй простыми человеческими словами, без заумных терминов. Говори на языке собеседника.
"""



@st.dialog("Источники")
def open_sources_window(sources):
    for title, url in sources:
        st.markdown(f"- [{title}]({url})")


def get_available_models(client):
    if "models_list" not in st.session_state:
        found = []
        try:
            for m in client.models.list():
                name = m.name.replace("models/", "")
                if ("flash" in name or "gemma" in name) and "image" not in name and "audio" not in name and "tts" not in name:
                    found.append(name)
        except Exception:
            pass
        found.sort(key=lambda x: (0 if "lite" in x else 1 if "flash" in x else 2))
        st.session_state.models_list = found or ["gemini-3.5-flash-lite", "gemini-3-flash-preview"]
    return st.session_state.models_list


def clean_query(text):
    stop_words = r"\b(привет|здравствуй|хай|ку|хикари|скажи|расскажи|подскажи|можешь|пожалуйста|пж|типа|короче)\b"
    cleaned = re.sub(stop_words, "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"[^\w\s\-]", " ", cleaned)
    cleaned = " ".join(cleaned.split())
    return cleaned if len(cleaned) >= 3 else text
def make_search_query(client, history_msgs, user_text):
    """Превращает короткий вопрос вроде «а когда она вышла?» в точный запрос для поиска"""
    recent = "\n".join(f"{m['role']}: {m['content'][:200]}" for m in history_msgs[-3:])
    try:
        res = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=(
                f"Перепиши последний вопрос пользователя в короткий поисковый запрос (3-6 слов) для Google, "
                f"раскрыв все местоимения («она», «эта игра», «он») из контекста переписки. "
                f"Выведи ТОЛЬКО сам поисковый запрос без кавычек и пояснений.\n\n"
                f"Контекст:\n{recent}\nВопрос: {user_text}"
            ),
        )
        q = res.text.strip()
        return q if 2 <= len(q) <= 80 else clean_query(user_text)
    except Exception:
        return clean_query(user_text)


def search_internet(query):
    links = []
    context_parts = []
    search_q = clean_query(query)
    q_encoded = urllib.parse.quote(search_q)

    for b in ("lite", "html"):
        try:
            with DDGS() as ddgs:
                for r in ddgs.text(search_q, backend=b, max_results=4):
                    title = r.get("title", "Источник")
                    url = r.get("href", "")
                    body = r.get("body", "")
                    if url and (title, url) not in links:
                        links.append((title, url))
                        context_parts.append(f"Источник: {title}\nОписание: {body}")
            if links:
                break
        except Exception:
            continue

    if not links:
        try:
            rss_url = f"https://google.com{q_encoded}&hl=ru&gl=RU&ceid=RU:ru"
            req = urllib.request.Request(rss_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                root = ET.fromstring(resp.read())
                for item in root.findall("./channel/item")[:4]:
                    title = item.findtext("title") or "Статья"
                    url = item.findtext("link") or ""
                    if url:
                        links.append((title, url))
                        context_parts.append(f"Статья: {title}")
        except Exception:
            pass

    if not links:
        try:
            wiki_url = f"https://wikipedia.org{q_encoded}&limit=3&format=json"
            req = urllib.request.Request(wiki_url, headers={"User-Agent": "HikariBot/1.0 (contact@example.com)"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                titles = data if len(data) > 1 else []
                urls = data if len(data) > 3 else []
                for title, url in zip(titles, urls):
                    links.append((f"{title} — Википедия", url))
                    context_parts.append(f"Статья из Википедии: {title}")
        except Exception:
            pass

    if not links:
        links.append((f"Поиск в Google: {search_q}", f"https://google.com{q_encoded}"))
        links.append((f"Поиск в Википедии: {search_q}", f"https://wikipedia.org{q_encoded}"))

    return "\n\n".join(context_parts), links


if "client" not in st.session_state:
    st.session_state.client = genai.Client()

current_chat = st.session_state.chats[st.session_state.current_chat_id]
st.session_state.messages = current_chat["messages"]
async def _tts_bytes(text):
    clean_text = re.sub(r"[*#`~_]", "", text)
    communicate = edge_tts.Communicate(clean_text, "ru-RU-SvetlanaNeural", rate="-5%", pitch="+3Hz")
    audio_data = b""
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data += chunk["data"]
    return audio_data

def make_voice(text):
    try:
        return asyncio.run(_tts_bytes(text))
    except Exception:
        return None

def show(m, idx):
    st.markdown(m["content"])
    if m.get("audio"):
        st.audio(m["audio"], format="audio/mp3")
    if m.get("sources"):
        with st.popover("••"):
            if st.button("Посмотреть источники", key=f"src_btn_{idx}"):
                open_sources_window(m["sources"])


for idx, m in enumerate(st.session_state.messages):
    with st.chat_message(m["role"]):
        show(m, idx)

voice_input = st.audio_input("Сказать голосом")
if prompt := st.chat_input("Напиши Хикари или прикрепи фото...", accept_file=True, file_type=["png", "jpg", "jpeg", "webp"]):
    text = prompt.text or "Посмотри на это изображение и помоги разобраться."
    uploaded_file = prompt.files[0] if prompt.files else None
    img_bytes = uploaded_file.getvalue() if uploaded_file else None
    img_mime = uploaded_file.type if uploaded_file else None
    img_b64 = base64.b64encode(img_bytes).decode("utf-8") if img_bytes else None

    user_msg = {"role": "user", "content": text}
    if img_b64:
        user_msg["image_b64"] = img_b64

    if current_chat["title"] == "Новый чат":
        short_title = text[:26] + ("..." if len(text) > 26 else "")
        current_chat["title"] = short_title

    st.session_state.messages.append(user_msg)
    save_memory(st.session_state.messages)
    with st.chat_message("user"):
        show(user_msg, len(st.session_state.messages) - 1)

    sources = []
    today_str = datetime.date.today().strftime("%d.%m.%Y")
    prompt_to_send = f"(Сегодня {today_str}). Вопрос пользователя: {text}"

    with st.spinner("Хикари подбирает слова..."):
               is_question = "?" in text or bool(re.search(r"\b(кто|что|где|когда|почему|зачем|как|какие|какой|сколько|найди|поищи|расскажи|объясни|реши|помоги|посоветуй|покажи)\b", text, re.IGNORECASE))
               about_hikari = bool(re.search(r"\b(ты|тебе|тебя|твой|твоя|твоё|твои|у тебя)\b", text, re.IGNORECASE))
               if use_search and not img_bytes and is_question and not about_hikari:
                smart_q = make_search_query(st.session_state.client, st.session_state.messages[:-1], text)
                context, sources = search_internet(smart_q)
                if context:
                    prompt_to_send = (
                    f"Сегодня {today_str}. Вот свежие результаты поиска по запросу «{smart_q}»:\n{context}\n\n"
                    f"Опирайся В ПЕРВУЮ ОЧЕРЕДЬ на эти свежие данные. Дай подробный ответ на вопрос: {text}"
                )

               history = []
               for msg in st.session_state.messages[-7:-1]:
                   role = "user" if msg["role"] == "user" else "model"
                   history.append(types.Content(role=role, parts=[types.Part.from_text(text=msg["content"])]))
               user_parts = []
               if img_bytes and img_mime:
                   user_parts.append(types.Part.from_bytes(data=img_bytes, mime_type=img_mime))
               if voice_input:
                   user_parts.append(types.Part.from_bytes(data=voice_input.getvalue(), mime_type="audio/wav"))
               user_parts.append(types.Part.from_text(text=prompt_to_send))
               history.append(types.Content(role="user", parts=user_parts))

               reply = None
               last_error = ""
               models_to_try = get_available_models(st.session_state.client)

               for model_name in models_to_try:
                   try:
                       if model_name.startswith("gemma"):
                        gemma_history = [
                        types.Content(role="user", parts=[types.Part.from_text(text=f"Инструкция: {характер}")]),
                        types.Content(role="model", parts=[types.Part.from_text(text="...хорошо, я поняла.")]),
                    ] + history
                        answer = st.session_state.client.models.generate_content(
                        model=model_name,
                        contents=gemma_history,
                    )
                       else:
                           answer = st.session_state.client.models.generate_content(
                               model=model_name,
                               contents=history,
                               config=types.GenerateContentConfig(system_instruction=характер),
                    )
                       reply = {
                           "role": "assistant",
                           "content": answer.text,
                           "sources": sources,
                           "audio": make_voice(answer.text) if use_voice else None,
                       }
                       break
                   except Exception as e:
                       last_error = e
                       time.sleep(1)

               if not reply:
                   reply = {
                       "role": "assistant",
                       "content": f"... Кажется, серверы сейчас сильно заняты. Попробуй отправить сообщение ещё раз через пару секунд (`{last_error}`)",
                "sources": [],
            }

    st.session_state.messages.append(reply)
    save_memory(st.session_state.messages)
    with st.chat_message("assistant"):
        show(reply, len(st.session_state.messages) - 1)
