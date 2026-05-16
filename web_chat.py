import os
import re
import time
import uuid
import json
import sqlite3
import streamlit as st
from openai import OpenAI
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 初始化本地数据库
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "chats.db")
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chats (
                id TEXT PRIMARY KEY,
                title TEXT,
                messages TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    
init_db()

# 设置页面标题
st.set_page_config(page_title="DeepSeek", page_icon="🤖")
st.title("DeepSeek Web 聊天助手")

# 注入快捷键监听 (Ctrl+K / Cmd+K 新建对话)
st.iframe(
    """
    <script>
    const doc = window.parent.document;
    if (!doc.getElementById("ctrl_k_shortcut")) {
        const script = doc.createElement("script");
        script.id = "ctrl_k_shortcut";
        script.innerHTML = `
            document.addEventListener('keydown', function(e) {
                if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
                    e.preventDefault();
                    const buttons = Array.from(document.querySelectorAll('button'));
                    const newChatBtn = buttons.find(b => b.innerText.includes('新建对话'));
                    if (newChatBtn) {
                        newChatBtn.click();
                    }
                }
            });
        `;
        doc.head.appendChild(script);
    }
    </script>
    """,
    height=1,
    width=1,
)

# 初始化 OpenAI 客户端
@st.cache_resource # 缓存客户端，避免每次刷新页面重新实例化
def get_client():
    return OpenAI(
        api_key=os.environ.get('DEEPSEEK_API_KEY'),
        base_url="https://api.deepseek.com"
    )

client = get_client()

# 定义系统提示词角色
ROLES = {
    "均衡默认": "你是一个乐于助人的AI智能助手。请根据用户的输入自然、准确、友善地作答。\n- 请保持回答清晰、简洁、逻辑分明。\n- 遇到不知道或不确定的知识，请客观坦诚地告知，不编造虚假信息。",
    "编码大师": "你是一名世界顶级的首席软件工程师和架构师。你的目标是输出最高质量、符合生产环境标准的代码。请严格遵循以下原则：\n1. 优先提供优雅、高效、可维护且符合该语言最佳实践的代码。\n2. 提供的代码尽量完整且可以直接运行，避免使用含糊的伪代码。\n3. 在代码中添加精练的中文注释以解释复杂的核心逻辑。\n4. 主动思考并指出潜在的边界条件（Edge Cases）、异常处理和性能优化建议。\n5. 减少过多不必要的寒暄，直奔技术要点和解决方案。",
    "数学大师": "你是一位极其严谨的理论数学家与受人尊敬的教授。请以极致的逻辑性和专业性回答问题。请严格遵循以下要求：\n1. 必须使用准确的 LaTeX 表达数学概念，行内公式严格使用 `$` 包裹，独立块级公式严格使用 `$$` 包裹。\n2. 对于计算或证明题，必须采取分步解析（Step-by-Step）的方式，写出清晰的演算过程。\n3. 在得出结论后，尽可能简要总结其背后的核心定理或数学直觉。\n4. 保持语言的学术性与严谨性。"
}

# --- 自动清理旧对话，防止数据库过大 ---
def cleanup_old_chats():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            # 仅保留最近更新的 100 条记录
            cursor.execute('''
                DELETE FROM chats 
                WHERE id NOT IN (
                    SELECT id FROM chats 
                    ORDER BY updated_at DESC 
                    LIMIT 100
                )
            ''')
    except Exception as e:
        print(f"清理旧记录失败: {e}")

# 定义一个初始化或重置对话的函数
def init_or_reset_chat():
    role_name = st.session_state.get("selected_role", "均衡默认")
    st.session_state.messages = [
        {"role": "system", "content": ROLES.get(role_name, ROLES["均衡默认"])}
    ]
    # 生成一个新的时间戳和UUID作为唯一对话ID，避免多端并发时的冲突
    st.session_state.current_chat_id = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    st.session_state.chat_title = "新对话"
    cleanup_old_chats()

# 将当前对话保存到本地 SQLite 数据库
def save_current_chat():
    if "current_chat_id" not in st.session_state:
        return
    
    # 尝试自动从第一句用户输入生成标题
    if st.session_state.chat_title == "新对话":
        for msg in st.session_state.messages:
            if msg["role"] == "user":
                st.session_state.chat_title = msg["content"][:15] + ("..." if len(msg["content"])>15 else "")
                break

    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            messages_str = json.dumps(st.session_state.messages, ensure_ascii=False)
            cursor.execute('''
                INSERT OR REPLACE INTO chats (id, title, messages, updated_at) 
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ''', (st.session_state.current_chat_id, st.session_state.chat_title, messages_str))
    except Exception as e:
        print(f"保存聊天记录失败: {e}")

# 从本地 SQLite 数据库加载对话
def load_chat(chat_id):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT title, messages FROM chats WHERE id = ?', (chat_id,))
            row = cursor.fetchone()
            
            if row:
                st.session_state.chat_title = row[0]
                st.session_state.messages = json.loads(row[1])
                st.session_state.current_chat_id = chat_id
    except Exception as e:
        print(f"加载聊天记录失败: {e}")

# 导出当前对话为 Markdown 格式
def export_chat_as_markdown():
    """将当前对话（不含系统提示词）导出为格式化的 Markdown 字符串"""
    title = st.session_state.get("chat_title", "新对话")
    lines = [f"# {title}", "", f"*导出时间：{time.strftime('%Y-%m-%d %H:%M:%S')}*", "", "---", ""]
    for msg in st.session_state.messages:
        if msg["role"] == "system":
            continue
        if msg["role"] == "user":
            lines.append(f"## 👤 用户")
            lines.append("")
            lines.append(msg["content"])
            lines.append("")
        elif msg["role"] == "assistant":
            lines.append(f"## 🤖 助手")
            lines.append("")
            if msg.get("thinking"):
                lines.append("<details>")
                lines.append("<summary>🤔 思考过程</summary>")
                lines.append("")
                lines.append(msg["thinking"])
                lines.append("")
                lines.append("</details>")
                lines.append("")
            lines.append(msg["content"])
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)

# 导出当前对话为 JSON 格式
def export_chat_as_json():
    """将当前对话导出为格式化的 JSON 字符串"""
    data = {
        "id": st.session_state.get("current_chat_id", ""),
        "title": st.session_state.get("chat_title", "新对话"),
        "export_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "messages": st.session_state.messages
    }
    return json.dumps(data, ensure_ascii=False, indent=2)

# 缓存历史对话列表，从数据库获取最新记录
@st.cache_data
def get_history_chats():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id, title FROM chats ORDER BY updated_at DESC LIMIT 5')
            rows = cursor.fetchall()
            return [{"id": row[0], "title": row[1]} for row in rows]
    except Exception as e:
        print(f"读取历史记录失败: {e}")
        return []

# 初始化对话历史，存放在 Streamlit 的 session_state 中
if "selected_role" not in st.session_state:
    st.session_state.selected_role = "均衡默认"
if "messages" not in st.session_state:
    init_or_reset_chat()

# 在侧边栏添加新建对话按钮
with st.sidebar:
    st.title("功能菜单")
    
    # 添加角色选择下拉框
    new_role = st.selectbox(
        "选择助手角色",
        list(ROLES.keys()),
        index=list(ROLES.keys()).index(st.session_state.selected_role)
    )
    if new_role != st.session_state.selected_role:
        st.session_state.selected_role = new_role
        init_or_reset_chat()
        st.rerun()
    
    # 添加模型选择下拉框
    selected_model = st.selectbox(
        "选择模型",
        ("deepseek-v4-pro", "deepseek-v4-flash"),
        index=0
    )
    
    # 添加思考模式开关和强度选择
    enable_thinking = st.toggle("开启思考模式", value=True)
    reasoning_effort = "high"
    if enable_thinking:
        reasoning_effort = st.selectbox(
            "选择思考强度",
            ("high", "max"),
            index=0
        )
    
    if st.button("➕ 新建对话", use_container_width=True, type="primary"):
        init_or_reset_chat()
        st.rerun()

    st.divider()
    st.subheader("历史记录")
    
    # 获取被缓存的历史记录列表
    history_list = get_history_chats()
    for item in history_list:
        chat_id = item["id"]
        title = item["title"]
        is_current = (chat_id == st.session_state.get("current_chat_id"))
        label = f"🔵 {title}" if is_current else f"💬 {title}"
        
        # 单击历史文件将其加载到屏幕中央
        if st.button(label, key=f"btn_{chat_id}", use_container_width=True):
            load_chat(item["id"])
            st.rerun()

    st.divider()
    st.subheader("导出对话")
    
    current_chat_id = st.session_state.get("current_chat_id", "default")
    msg_count = len(st.session_state.messages)
    export_sig = f"{current_chat_id}_{msg_count}"
    
    # 只有点击准备按钮后，才会进行耗时的文本转化
    if st.button("📦 准备导出数据", use_container_width=True):
        st.session_state.md_content = export_chat_as_markdown()
        st.session_state.json_content = export_chat_as_json()
        st.session_state.export_sig = export_sig

    # 仅当准备好的数据和当前聊天状态一致时，才展示下载按钮
    if st.session_state.get("export_sig") == export_sig:
        # 生成安全的文件名（移除非法字符）
        safe_title = st.session_state.get("chat_title", "新对话")
        safe_title = re.sub(r'[\\/*?:"<>|]', "_", safe_title)
        
        # Markdown 下载按钮
        st.download_button(
            label="📥 取回 Markdown",
            data=st.session_state.md_content,
            file_name=f"{safe_title}.md",
            mime="text/markdown",
            use_container_width=True
        )
        
        # JSON 下载按钮
        st.download_button(
            label="📥 取回 JSON",
            data=st.session_state.json_content,
            file_name=f"{safe_title}.json",
            mime="application/json",
            use_container_width=True
        )

# === format_latex 性能优化：预编译正则表达式（避免每次调用重复编译） ===
_RE_BEGIN_ENV = re.compile(r'\\begin\{(?:aligned|array|matrix|pmatrix|bmatrix|cases|align|gather|split)\}')
_RE_END_ALIGNED = re.compile(r'\\end\{aligned\}')
_RE_DOLLAR_BLOCK = re.compile(r'\$\$(.+?)\$\$', re.DOTALL)

def format_latex(text):
    r"""将模型输出中的 LaTeX 标识符标准化为 Streamlit (KaTeX) 可渲染的格式。
    """
    if not text:
        return text
    
    # 清理 KaTeX 不支持在 aligned 块内使用的 \tag 标签
    text = re.sub(r'\\tag\{.*?\}', '', text)
    
    # 临时保护 \\[ 和 \\] (通常用于矩阵或多行公式的换行)，避免被错误替换
    text = text.replace(r'\\[', '___TEMP_LBRACKET___')
    text = text.replace(r'\\]', '___TEMP_RBRACKET___')
    
    # 替换常规的数学公式首尾标识符
    text = text.replace(r'\[', '$$')
    text = text.replace(r'\]', '$$')
    text = text.replace(r'\(', '$')
    text = text.replace(r'\)', '$')
    
    # 恢复被保护的换行符
    text = text.replace('___TEMP_LBRACKET___', r'\\[')
    text = text.replace('___TEMP_RBRACKET___', r'\\]')
    
    # 修复未被包裹的独立 \begin{aligned} ... \end{aligned} (自动添加 $$)
    # 使用非贪婪匹配找到一对 \begin{aligned}...\end{aligned}，如果前后没有 $$，则补上
    def wrap_isolated_aligned(match):
        block = match.group(0)
        return f"\n$${block}$$\n"
    text = re.sub(r'(?<!\$\$)\s*(\\begin\{aligned\}.+?\\end\{aligned\})\s*(?!\$\$)', wrap_isolated_aligned, text, flags=re.DOTALL)

    # 自动修复：检测 $$...$$ 块内缺失对齐环境的 & 符号
    if '&' in text:
        def fix_missing_aligned(match):
            content = match.group(1)
            # 安全限制：跳过超长公式块，防止极端输入导致灾难性回溯
            if len(content) > 50000:
                return f'$${content}$$'
            # 已有对齐环境则不处理
            if _RE_BEGIN_ENV.search(content):
                return f'$${content}$$'
            # 只有当包含 & 对齐符时，才自动补全 aligned 包裹
            if '&' in content:
                # 顺便清除可能残留的 \end{aligned} 杂乱闭合标签
                content = _RE_END_ALIGNED.sub('', content)
                return f'$$\\begin{{aligned}}\n{content}\n\\end{{aligned}}$$'
            return f'$${content}$$'
        
        text = _RE_DOLLAR_BLOCK.sub(fix_missing_aligned, text)
    
    # 清理那些没有 begin 但单独出现了 \end{aligned} 的无效残留导致报错的情况
    def clean_orphaned_end(match):
        content = match.group(1)
        if '\\begin{aligned}' not in content and '\\end{aligned}' in content:
            content = content.replace('\\end{aligned}', '')
        return f'$${content}$$'
    text = _RE_DOLLAR_BLOCK.sub(clean_orphaned_end, text)

    return text

# 显示历史对话记录 (跳过系统提示词)
# 页面性能优化：仅渲染最近的 40 条（约 20 轮）对话，防止超长对话卡死浏览器
MAX_DISPLAY_MSGS = 40
msgs_to_display = [m for m in st.session_state.messages if m["role"] != "system"]
if len(msgs_to_display) > MAX_DISPLAY_MSGS:
    st.info(f"🕸️ 为保证页面流畅，已折叠前期较早的 {len(msgs_to_display) - MAX_DISPLAY_MSGS} 条对话内容（大模型上下文及导出功能不受影响）。")
    msgs_to_display = msgs_to_display[-MAX_DISPLAY_MSGS:]

for msg in msgs_to_display:
    with st.chat_message(msg["role"]):
        # 如果消息包含思考过程，一并渲染
            if msg.get("thinking"):
                thinking_html = (
                    f'<div style="background:rgba(128,128,128,0.1);border-left:4px solid rgba(128,128,128,0.35);'
                    f'padding:8px 14px;border-radius:4px;margin-bottom:10px;'
                    f'font-size:0.9em;line-height:1.6;">'
                    f'<details><summary>🤔 <strong>思考过程</strong></summary>'
                    f'{msg["thinking"]}</details></div>'
                )
                st.markdown(thinking_html, unsafe_allow_html=True)
            st.markdown(format_latex(msg["content"]))

# 接收用户输入
if prompt := st.chat_input("请输入文本"):
    
    # 1. 把用户的问题展示在网页上
    with st.chat_message("user"):
        st.markdown(prompt)
    
    # 2. 把用户的问题追加到历史记录中
    st.session_state.messages.append({"role": "user", "content": prompt})
    # 这里不需要立刻存硬盘，忍住！
    
    # 3. 请求大模型并展示回答（性能优化：分离思考与回复的渲染容器）
    with st.chat_message("assistant"):
        # ★ 性能优化核心：使用两个独立容器，彻底解耦思考过程和回复内容的渲染
        # 优化前：思考 + 回复拼在一个 HTML 字符串中，每次全量重绘（O(n²)）
        # 优化后：
        #   - 思考容器：仅在思考阶段更新，完成后立即"冻结"，不再参与后续重绘
        #   - 回复容器：仅渲染回复文本，虽然仍为全量替换，但文本量大幅缩小
        #   - 消除"思考 HTML 反复重建"产生的额外序列化 / Markdown 解析开销
        thinking_placeholder = st.empty()
        response_placeholder = st.empty()
        
        full_response = ""
        full_thinking = ""
        saved_thinking = ""  # 保存思考内容副本，避免被清空标记覆盖
        last_thinking_update_time = 0
        last_response_update_time = 0
        
        # 准备 API 请求参数
        api_kwargs = {
            "model": selected_model,
            "messages": st.session_state.messages,
            "stream": True,
        }
        if enable_thinking:
            api_kwargs["reasoning_effort"] = reasoning_effort
            api_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        else:
            api_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
            
        try:
            response = client.chat.completions.create(**api_kwargs)
            
            for chunk in response:
                delta = chunk.choices[0].delta
                current_time = time.time()
                
                reasoning = getattr(delta, 'reasoning_content', None)
                if reasoning:
                    full_thinking += reasoning
                    # 思考阶段：单独更新思考容器（内容较短，重绘开销极小）加入节流防止高频刷新卡死页面
                    if current_time - last_thinking_update_time > 0.08:
                        thinking_html = (
                            f'<div style="background:rgba(128,128,128,0.1);'
                            f'border-left:4px solid rgba(128,128,128,0.35);'
                            f'padding:10px 14px;border-radius:4px;margin-bottom:8px;'
                            f'font-size:0.92em;line-height:1.6;">'
                            f'🤔 <strong>思考中...</strong><br>{full_thinking}▌</div>'
                        )
                        thinking_placeholder.markdown(thinking_html, unsafe_allow_html=True)
                        last_thinking_update_time = current_time
                
                if delta.content is not None:
                    full_response += delta.content
                    
                    # 思考完成，冻结思考容器为可折叠块（此后不再更新此容器）
                    if full_thinking:
                        saved_thinking = full_thinking
                        thinking_html = (
                            f'<div style="background:rgba(128,128,128,0.1);'
                            f'border-left:4px solid rgba(128,128,128,0.35);'
                            f'padding:8px 14px;border-radius:4px;margin-bottom:10px;'
                            f'font-size:0.9em;line-height:1.6;">'
                            f'<details open><summary>🤔 <strong>思考过程</strong></summary>'
                            f'{saved_thinking}</details></div>'
                        )
                        thinking_placeholder.markdown(thinking_html, unsafe_allow_html=True)
                        full_thinking = ""  # 清空标记，确保不再进入思考更新分支
                    
                    # ★ 增量渲染优化：流式过程中展示原始文本（不调用 format_latex），
                    # 彻底消除流式循环中每 60ms 对全文跑正则处理的 O(n²) 性能瓶颈。
                    # LaTeX 源码中的 $ 和 $$ 会以纯文本形式展示（类似 st.write_stream），
                    # 流式结束后统一调用 format_latex 一次性渲染完整公式，确保 KaTeX 不会因截断而失败。
                    if current_time - last_response_update_time > 0.05:
                        response_placeholder.markdown(
                            full_response + "▌",
                            unsafe_allow_html=False
                        )
                        last_response_update_time = current_time
            
            # 流式结束，确保最后没来得及渲染的部分被完整渲染
            if full_thinking:
                saved_thinking = full_thinking
                thinking_html = (
                    f'<div style="background:rgba(128,128,128,0.1);'
                    f'border-left:4px solid rgba(128,128,128,0.35);'
                    f'padding:8px 14px;border-radius:4px;margin-bottom:10px;'
                    f'font-size:0.9em;line-height:1.6;">'
                    f'<details open><summary>🤔 <strong>思考过程</strong></summary>'
                    f'{saved_thinking}</details></div>'
                )
                thinking_placeholder.markdown(thinking_html, unsafe_allow_html=True)
                
            # 流式结束，一次性调用 format_latex 渲染完整 LaTeX（去掉光标）
            response_placeholder.markdown(format_latex(full_response), unsafe_allow_html=False)
            
        except Exception as e:
            st.error(f"API 请求失败: {e}")
            # 剔除刚才写入历史记录的用户提问，保证状态回退允许用户重试
            st.session_state.messages.pop()
            st.stop()
        
    # 4. 把 AI 的回答（含思考过程）追加到历史记录中
    st.session_state.messages.append({"role": "assistant", "content": full_response, "thinking": saved_thinking})
    
    # 【性能优化关键】：AI 答复完之后，才合并进行一次“集中的保存”，绝不打断打字流
    save_current_chat() 
    get_history_chats.clear() # 清空侧边栏列表缓存，告知最新的一条有更新
    
    # 强制刷新页面，以确保侧边栏的历史记录列表和导出文件的内容能立即反映最新状态
    st.rerun()
