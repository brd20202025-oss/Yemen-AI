import os
import re
import sqlite3
import json
import urllib.request
import urllib.error
from difflib import SequenceMatcher
from threading import Thread
from datetime import datetime

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.scrollview import ScrollView
from kivy.uix.popup import Popup
from kivy.uix.gridlayout import GridLayout
from kivy.utils import get_color_from_hex

APP_BG = "#10131A"
PANEL_BG = "#191E29"
USER_BUBBLE = "#254C72"
BOT_BUBBLE = "#252B38"
ACCENT = "#7C8CFF"
TEXT = "#F3F5FA"
MUTED = "#AAB2C5"

def rtl(text):
    """Best-effort Arabic display. Core app still works if optional shaping fails."""
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(str(text)))
    except Exception:
        return str(text)

class MemoryStore:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS memories(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS learned_qa(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL UNIQUE,
            answer TEXT NOT NULL,
            topic TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )""")
        self.db.commit()

    def add_message(self, role, content):
        now = datetime.now().isoformat(timespec="seconds")
        self.db.execute("INSERT INTO messages(role,content,created_at) VALUES(?,?,?)",
                        (role, content, now))
        self.db.commit()

    def messages(self, limit=200):
        cur = self.db.execute(
            "SELECT role,content FROM messages ORDER BY id DESC LIMIT ?", (limit,))
        return list(reversed(cur.fetchall()))

    def add_memory(self, title, content, memory_id=None):
        now = datetime.now().isoformat(timespec="seconds")
        if memory_id:
            self.db.execute("UPDATE memories SET title=?,content=?,updated_at=? WHERE id=?",
                            (title.strip(), content.strip(), now, memory_id))
        else:
            self.db.execute(
                "INSERT INTO memories(title,content,created_at,updated_at) VALUES(?,?,?,?)",
                (title.strip(), content.strip(), now, now))
        self.db.commit()

    def list_memories(self):
        return self.db.execute(
            "SELECT id,title,content FROM memories ORDER BY updated_at DESC").fetchall()

    def delete_memory(self, memory_id):
        self.db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        self.db.commit()

    def clear_messages(self):
        self.db.execute("DELETE FROM messages")
        self.db.commit()

    def save_qa(self, question, answer, topic=""):
        now = datetime.now().isoformat(timespec="seconds")
        self.db.execute(
            "INSERT INTO learned_qa(question,answer,topic,created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(question) DO UPDATE SET answer=excluded.answer, "
            "topic=excluded.topic, created_at=excluded.created_at",
            (question.strip(), answer.strip(), topic.strip(), now))
        self.db.commit()

    def find_qa(self, prompt):
        rows = self.db.execute("SELECT question,answer FROM learned_qa").fetchall()
        q = " ".join(prompt.lower().split())
        best, score = None, 0.0
        for question, answer in rows:
            candidate = " ".join(question.lower().split())
            s = SequenceMatcher(None, q, candidate).ratio()
            if s > score:
                best, score = answer, s
        return (best, score) if score >= 0.58 else (None, score)

    def learned_count(self):
        return self.db.execute("SELECT COUNT(*) FROM learned_qa").fetchone()[0]

    def close(self):
        self.db.close()

class OfflineEngine:
    """
    Safe baseline responder: offline and transparent, but NOT a trained language model.
    Replace generate() with a real on-device model runtime to obtain general AI abilities.
    """
    def __init__(self, store):
        self.store = store

    def generate(self, prompt):
        q = prompt.strip()
        low = q.lower()
        memories = self.store.list_memories()
        cached_answer, cached_score = self.store.find_qa(q)
        if cached_answer:
            return ("إجابة من المعرفة المحفوظة محليًا "
                    f"(مطابقة تقريبية {int(cached_score * 100)}%):\\n{cached_answer}")

        if any(x in low for x in ["السلام عليكم", "مرحبا", "مرحباً", "هلا", "hello", "hi"]):
            return "وعليكم السلام! أنا Smart AI. أعمل حاليًا بالمحرك المحلي الأساسي، ويمكنك حفظ معلومات في الذاكرة من زر «الذاكرة»."
        if any(x in low for x in ["من أنت", "من انت", "ما اسمك", "اسمك"]):
            return ("أنا Smart AI، تطبيق محادثة يعمل دون إنترنت. النسخة الحالية تتضمن ذاكرة محلية، "
                    "لكن محرك الإجابة الأساسي ليس نموذجًا لغويًا مدرّبًا.")
        if low.startswith(("لخص ", "لخص:", "تلخيص ")):
            text = re.sub(r"^(لخص|تلخيص)\s*[:：]?\s*", "", q)
            sentences = [s.strip() for s in re.split(r"(?<=[.!؟])\s+|\n", text) if s.strip()]
            if not sentences:
                return "أرسل النص بعد كلمة «لخص» لأحاول اختصاره."
            return "ملخص أولي (ليس تلخيصًا بنموذج لغوي): " + " ".join(sentences[:2])
        if any(x in low for x in ["ذاكرتي", "ماذا تتذكر", "ماذا تعرف عني"]):
            if not memories:
                return "لا توجد معلومات محفوظة في الذاكرة بعد. افتح «الذاكرة» لإضافة معلومات تختار حفظها."
            return "المعلومات المحفوظة:\n" + "\n".join(
                f"• {m[1]}: {m[2]}" for m in memories[:10])
        return ("لا أملك في هذه النسخة نموذجًا لغويًا محليًا قادرًا على فهم هذا السؤال بشكل عام.\n\n"
                "ما أستطيع فعله الآن: حفظ المحادثات محليًا، إدارة الذاكرة، والرد على بعض الأوامر البسيطة. "
                "للحصول على إجابات ذكية حقيقية دون إنترنت، يجب دمج نموذج GGUF متوافق مع Android "
                "ومحرك استدلال مبنيّ للأجهزة المحمولة.")

class ChatBubble(BoxLayout):
    def __init__(self, role, content, **kwargs):
        super().__init__(orientation="vertical", size_hint_y=None,
                         padding=(dp(12), dp(9)), spacing=dp(4), **kwargs)
        self.bind(minimum_height=self.setter("height"))
        bg = USER_BUBBLE if role == "user" else BOT_BUBBLE
        self.canvas.before.clear()
        from kivy.graphics import Color, RoundedRectangle
        with self.canvas.before:
            Color(*get_color_from_hex(bg))
            self.rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(14)])
        self.bind(pos=self._update_rect, size=self._update_rect)
        label = Label(text=rtl("أنت" if role == "user" else "Smart AI"),
                      color=get_color_from_hex(ACCENT if role != "user" else "#D7E7FF"),
                      font_size="12sp", size_hint_y=None, height=dp(20),
                      halign="right", valign="middle")
        label.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        self.add_widget(label)
        body = Label(text=rtl(content), color=get_color_from_hex(TEXT),
                     font_size="15sp", size_hint_y=None, halign="right", valign="top")
        body.bind(width=lambda inst, val: setattr(inst, "text_size", (max(1, val), None)))
        body.bind(texture_size=lambda inst, val: setattr(inst, "height", val[1] + dp(4)))
        self.add_widget(body)

    def _update_rect(self, *_):
        self.rect.pos = self.pos
        self.rect.size = self.size

class SmartAIApp(App):
    title = "Yemen AI"

    def build(self):
        Window.clearcolor = get_color_from_hex(APP_BG)
        self.store = MemoryStore(os.path.join(self.user_data_dir, "smartai.db"))
        self.engine = OfflineEngine(self.store)
        self.last_user_message = ""
        self.chat_root = None
        self.show_welcome()
        return self.root_box

    def make_button(self, label, callback, color=ACCENT, height=dp(52)):
        btn = Button(text=label, size_hint_y=None, height=height,
                     background_normal="", background_color=get_color_from_hex(color),
                     color=get_color_from_hex("#FFFFFF"), font_size="16sp")
        btn.bind(on_release=lambda *_: callback())
        return btn

    def show_welcome(self):
        self.root_box = BoxLayout(orientation="vertical", padding=dp(24), spacing=dp(16))
        self.root_box.add_widget(Label(text="Yemen AI", color=get_color_from_hex(TEXT),
                                       font_size="34sp", bold=True, size_hint_y=None,
                                       height=dp(70)))
        self.root_box.add_widget(Label(
            text="يمن AI\\\\nمساعدك المحلي وذاكرتك الخاصة",
            color=get_color_from_hex(ACCENT), font_size="20sp",
            halign="center", valign="middle"))
        self.root_box.add_widget(Label(
            text=f"يعمل دون إنترنت بعد حفظ المعرفة\\\\nمعلومات محفوظة: {self.store.learned_count()}",
            color=get_color_from_hex(MUTED), font_size="14sp",
            halign="center", valign="middle", size_hint_y=None, height=dp(62)))
        self.root_box.add_widget(self.make_button("ابدأ المحادثة", self.start_chat))
        self.root_box.add_widget(self.make_button("تعلّم من السحابة", self.open_cloud_learning, "#394B78"))
        self.root_box.add_widget(self.make_button("إدارة الذاكرة", self.open_memories, PANEL_BG))
        self.root_box.add_widget(Label(
            text="برمجة وتوجيه عبدالحق يحيى الغولي\\\\nجميع الحقوق محفوظة",
            color=get_color_from_hex(MUTED), font_size="11sp",
            halign="center", valign="bottom", size_hint_y=None, height=dp(48)))
        self.root_box.add_widget(Label(text="",
                                       size_hint_y=1))
        if self.chat_root is not None:
            self.chat_root.clear_widgets()
        # Main app swaps this root in-place so Android keeps the same app instance.
        if hasattr(self, "_app_root") and self._app_root is not None:
            self._app_root.clear_widgets()
            self._app_root.add_widget(self.root_box)
        else:
            self._app_root = self.root_box

    def start_chat(self):
        chat = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))
        header = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        title = Label(text="Yemen AI  •  محلي", color=get_color_from_hex(TEXT),
                      bold=True, font_size="19sp", halign="right", valign="middle")
        title.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        header.add_widget(title)
        header.add_widget(self.make_button("الرئيسية", self.show_welcome, PANEL_BG, dp(42)))
        header.add_widget(self.make_button("الذاكرة", self.open_memories, ACCENT, dp(42)))
        chat.add_widget(header)

        self.scroll = ScrollView(do_scroll_x=False, bar_width=dp(3))
        self.chat = GridLayout(cols=1, spacing=dp(8), size_hint_y=None, padding=(0, dp(4)))
        self.chat.bind(minimum_height=self.chat.setter("height"))
        self.scroll.add_widget(self.chat)
        chat.add_widget(self.scroll)

        self.status = Label(
            text="محلي دون إنترنت • إجابات السحابة المحفوظة تعمل دون اتصال",
            color=get_color_from_hex(MUTED), font_size="10sp",
            size_hint_y=None, height=dp(24), halign="center", valign="middle")
        self.status.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        chat.add_widget(self.status)

        composer = BoxLayout(size_hint_y=None, height=dp(54), spacing=dp(8))
        self.input = TextInput(hint_text="اكتب رسالتك هنا...", multiline=True,
                               size_hint_x=1, font_size="15sp",
                               foreground_color=get_color_from_hex(TEXT),
                               hint_text_color=get_color_from_hex(MUTED),
                               background_normal="", background_active="",
                               background_color=get_color_from_hex(PANEL_BG),
                               cursor_color=get_color_from_hex(ACCENT),
                               padding=(dp(12), dp(12)))
        composer.add_widget(self.input)
        composer.add_widget(self.make_button("إرسال", self.send_message, ACCENT, dp(52)))
        chat.add_widget(composer)

        self.chat_root = chat
        self._app_root.clear_widgets()
        self._app_root.add_widget(chat)
        self.load_history()
        if not self.store.messages():
            self.add_bubble("assistant",
                "أهلًا بك في يمن AI. يمكنك تعلّم أمثلة من السحابة ثم الرجوع إليها دون إنترنت. "
                "تنبيه: ما يُحفظ هنا هو معرفة وأمثلة نصية، وليس أوزان النموذج السحابي.")

    def open_cloud_learning(self):
        panel = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))
        panel.add_widget(Label(
            text="أدخل موضوعًا لتوليد أمثلة سؤال/جواب وحفظها على الجهاز.\\\\n"
                 "يلزم اتصال بالإنترنت ورمز Hugging Face. لا توجد خدمة مجانية بلا حدود.",
            color=get_color_from_hex(TEXT), size_hint_y=None, height=dp(88),
            halign="center"))
        topic_input = TextInput(hint_text="مثال: أساسيات البرمجة بلغة Python",
                                multiline=False, size_hint_y=None, height=dp(46))
        token_input = TextInput(hint_text="Hugging Face access token (hf_...)",
                                multiline=False, password=True,
                                size_hint_y=None, height=dp(46))
        panel.add_widget(topic_input)
        panel.add_widget(token_input)
        status = Label(text="الرمز لا يُحفظ في قاعدة البيانات؛ سيُستخدم لهذا الطلب فقط.",
                       color=get_color_from_hex(MUTED), size_hint_y=None, height=dp(42))
        panel.add_widget(status)
        buttons = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        learn = Button(text="ابدأ التعلّم", background_normal="",
                       background_color=get_color_from_hex(ACCENT))
        close = Button(text="إغلاق", background_normal="",
                       background_color=get_color_from_hex(PANEL_BG))
        buttons.add_widget(learn); buttons.add_widget(close); panel.add_widget(buttons)
        popup = Popup(title="تعلّم من السحابة", content=panel,
                      size_hint=(0.94, 0.66), auto_dismiss=False)
        close.bind(on_release=popup.dismiss)

        def begin(*_):
            topic = topic_input.text.strip()
            token = token_input.text.strip()
            if not topic or not token:
                status.text = "أدخل الموضوع والرمز أولًا."
                return
            learn.disabled = True
            status.text = "جارٍ طلب أمثلة من النموذج السحابي..."
            Thread(target=self._cloud_learning_worker,
                   args=(topic, token, popup, status, learn), daemon=True).start()
        learn.bind(on_release=begin)
        popup.open()

    def _cloud_learning_worker(self, topic, token, popup, status_label, learn_button):
        try:
            prompt = (
                "Create 8 useful educational question-answer pairs about this topic: " + topic + ". "
                "The answers must be in Arabic. Return ONLY a JSON array, no markdown. "
                'Each item must have keys "question" and "answer". Keep answers factual and concise.'
            )
            payload = {
                "model": "Qwen/Qwen2.5-7B-Instruct:fastest",
                "messages": [
                    {"role": "system", "content": "You create concise, accurate Arabic educational Q&A examples."},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 1800,
                "temperature": 0.3
            }
            req = urllib.request.Request(
                "https://router.huggingface.co/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Authorization": "Bearer " + token,
                         "Content-Type": "application/json"},
                method="POST")
            with urllib.request.urlopen(req, timeout=90) as response:
                data = json.loads(response.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
            match = re.search(r"\[.*\]", content, flags=re.S)
            if not match:
                raise ValueError("لم يُرجع النموذج قائمة JSON صالحة.")
            items = json.loads(match.group(0))
            saved = 0
            for item in items:
                question = str(item.get("question", "")).strip()
                answer = str(item.get("answer", "")).strip()
                if question and answer:
                    self.store.save_qa(question, answer, topic)
                    saved += 1
            if not saved:
                raise ValueError("لم تُستخرج أمثلة صالحة من الرد.")
            message = f"اكتمل التعلّم: حُفظ {saved} مثالًا عن «{topic}» على الجهاز. ستتوفر المطابقة التقريبية دون إنترنت."
        except urllib.error.HTTPError as exc:
            try:
                details = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                details = str(exc)
            message = f"فشل طلب السحابة (HTTP {exc.code}). تحقق من الرمز/النموذج/الحصة. {details}"
        except Exception as exc:
            message = f"تعذّر التعلّم: {exc}"
        def finish(*_):
            status_label.text = message
            learn_button.disabled = False
            if message.startswith("اكتمل"):
                self.status.text = f"المعرفة المحفوظة محليًا: {self.store.learned_count()} مثال"
        Clock.schedule_once(finish, 0)

    def add_bubble(self, role, content):
        bubble = ChatBubble(role, content, size_hint_x=1)
        self.chat.add_widget(bubble)
        Clock.schedule_once(lambda *_: setattr(self.scroll, "scroll_y", 0), 0.1)

    def load_history(self):
        for role, content in self.store.messages():
            self.add_bubble(role, content)

    def send_message(self):
        text = self.input.text.strip()
        if not text:
            return
        self.input.text = ""
        self.last_user_message = text
        self.store.add_message("user", text)
        self.add_bubble("user", text)
        answer = self.engine.generate(text)
        self.store.add_message("assistant", answer)
        self.add_bubble("assistant", answer)

    def open_memories(self):
        outer = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        scroller = ScrollView()
        listing = GridLayout(cols=1, spacing=dp(8), size_hint_y=None)
        listing.bind(minimum_height=listing.setter("height"))
        memories = self.store.list_memories()
        if not memories:
            listing.add_widget(Label(text="لا توجد معلومات محفوظة.", color=get_color_from_hex(TEXT),
                                     size_hint_y=None, height=dp(42)))
        for mid, title, content in memories:
            row = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(5))
            edit = Button(text=f"{title}\n{content[:55]}", halign="right",
                          background_normal="", background_color=get_color_from_hex(PANEL_BG),
                          color=get_color_from_hex(TEXT))
            edit.bind(on_release=lambda _btn, item=(mid, title, content): self.edit_memory(item, popup))
            row.add_widget(edit)
            delete = Button(text="حذف", size_hint_x=None, width=dp(62),
                            background_normal="", background_color=get_color_from_hex("#813E4A"))
            delete.bind(on_release=lambda _btn, item_id=mid: self.delete_memory(item_id, popup))
            row.add_widget(delete)
            listing.add_widget(row)
        scroller.add_widget(listing)
        outer.add_widget(scroller)
        controls = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        add = Button(text="إضافة معلومة", background_normal="",
                     background_color=get_color_from_hex(ACCENT))
        add.bind(on_release=lambda *_: self.edit_memory(None, popup))
        controls.add_widget(add)
        close = Button(text="إغلاق", background_normal="",
                       background_color=get_color_from_hex(PANEL_BG))
        controls.add_widget(close)
        outer.add_widget(controls)
        popup = Popup(title="الذاكرة المحلية", content=outer, size_hint=(0.94, 0.82),
                      background_color=get_color_from_hex(APP_BG))
        close.bind(on_release=popup.dismiss)
        popup.open()

    def edit_memory(self, item, parent_popup):
        existing_id, old_title, old_content = item if item else (None, "", "")
        panel = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))
        title_input = TextInput(text=old_title, hint_text="عنوان المعلومة",
                                size_hint_y=None, height=dp(48), multiline=False)
        content_input = TextInput(text=old_content, hint_text="المعلومة التي تريد حفظها",
                                  multiline=True)
        panel.add_widget(title_input)
        panel.add_widget(content_input)
        buttons = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        save = Button(text="حفظ", background_normal="", background_color=get_color_from_hex(ACCENT))
        cancel = Button(text="إلغاء", background_normal="", background_color=get_color_from_hex(PANEL_BG))
        buttons.add_widget(save)
        buttons.add_widget(cancel)
        panel.add_widget(buttons)
        dialog = Popup(title="تحرير الذاكرة", content=panel, size_hint=(0.92, 0.62))
        def do_save(*_):
            title = title_input.text.strip()
            content = content_input.text.strip()
            if title and content:
                self.store.add_memory(title, content, existing_id)
                dialog.dismiss()
                parent_popup.dismiss()
                self.open_memories()
        save.bind(on_release=do_save)
        cancel.bind(on_release=dialog.dismiss)
        dialog.open()

    def delete_memory(self, memory_id, parent_popup):
        self.store.delete_memory(memory_id)
        parent_popup.dismiss()
        self.open_memories()

    def confirm_clear_chat(self):
        panel = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(12))
        panel.add_widget(Label(text="هل تريد حذف سجل المحادثة؟\nلن تُحذف الذاكرة المحفوظة.",
                               color=get_color_from_hex(TEXT)))
        buttons = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        yes = Button(text="حذف", background_normal="", background_color=get_color_from_hex("#813E4A"))
        no = Button(text="إلغاء", background_normal="", background_color=get_color_from_hex(PANEL_BG))
        buttons.add_widget(yes); buttons.add_widget(no); panel.add_widget(buttons)
        popup = Popup(title="تأكيد الحذف", content=panel, size_hint=(0.85, 0.35))
        def clear(*_):
            self.store.clear_messages()
            self.chat.clear_widgets()
            popup.dismiss()
            self.add_bubble("assistant", "تم مسح سجل المحادثة من قاعدة البيانات المحلية.")
        yes.bind(on_release=clear)
        no.bind(on_release=popup.dismiss)
        popup.open()

    def on_stop(self):
        if hasattr(self, "store"):
            self.store.close()

if __name__ == "__main__":
    SmartAIApp().run()
