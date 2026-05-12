import os
import time
import json
import glob
import streamlit as st
from openai import OpenAI
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 创建历史对话存储目录
CHATS_DIR = "chats"
os.makedirs(CHATS_DIR, exist_ok=True)

# 设置页面标题
st.set_page_config(page_title="DeepSeek", page_icon="🤖")
st.title("DeepSeek Web 聊天助手")

# 注入隐藏的 JS 监听 Ctrl+K (或 Cmd+K) 快捷键新建对话
st.html(
    """
    <script>
    const doc = window.parent.document;
    if (!doc.getElementById('custom_shortcut_js')) {
        const script = doc.createElement('script');
        script.id = 'custom_shortcut_js';
        script.type = 'text/javascript';
        script.innerHTML = `
            document.addEventListener('keydown', function(e) {
                if ((e.ctrlKey || e.metaKey) && String(e.key).toLowerCase() === 'k') {
                    e.preventDefault();
                    // 查找内容包含“新建对话”的按钮并触发点击
                    const buttons = Array.from(document.querySelectorAll('button'));
                    const targetBtn = buttons.find(b => b.innerText.includes('新建对话'));
                    if (targetBtn) {
                        targetBtn.click();
                    }
                }
            });
        `;
        doc.head.appendChild(script);
    }
    </script>
    """
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

# 定义一个初始化或重置对话的函数
def init_or_reset_chat():
    role_name = st.session_state.get("selected_role", "均衡默认")
    st.session_state.messages = [
        {"role": "system", "content": ROLES.get(role_name, ROLES["均衡默认"])}
    ]
    # 生成一个新的时间戳作为对话ID
    st.session_state.current_chat_id = str(int(time.time() * 1000))
    st.session_state.chat_title = "新对话"

# 将当前对话保存到本地 JSON 文件
def save_current_chat():
    if "current_chat_id" not in st.session_state:
        return
    
    # 尝试自动从第一句用户输入生成标题
    if st.session_state.chat_title == "新对话":
        for msg in st.session_state.messages:
            if msg["role"] == "user":
                st.session_state.chat_title = msg["content"][:15] + ("..." if len(msg["content"])>15 else "")
                break

    file_path = os.path.join(CHATS_DIR, f"{st.session_state.current_chat_id}.json")
    data = {
        "id": st.session_state.current_chat_id,
        "title": st.session_state.chat_title,
        "messages": st.session_state.messages
    }
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# 从本地 JSON 文件加载对话
def load_chat(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        st.session_state.messages = data.get("messages", [])
        st.session_state.current_chat_id = data.get("id")
        st.session_state.chat_title = data.get("title", "未命名对话")

# 缓存历史对话列表，避免每次刷新重复读取全部文件
@st.cache_data
def get_history_chats():
    if not os.path.exists(CHATS_DIR):
        return []
    files = glob.glob(os.path.join(CHATS_DIR, "*.json"))
    files.sort(reverse=True)
    res = []
    # 最多只读取最近 5 条，大幅提升侧边栏加载速度
    for f in files[:5]:
        try:
            with open(f, "r", encoding="utf-8") as file:
                data = json.load(file)
                res.append({"id": data.get("id"), "title": data.get("title", "未命名对话"), "path": f})
        except Exception as e:
            print(f"解析历史记录出错 {f}: {e}")
            continue
    return res

# 简单的密码验证逻辑
def check_password():
    # 尝试从环境变量获取密码，如果没有设置则默认密码为 "123456" （强烈建议在 .env 中设置）
    CORRECT_PASSWORD = os.environ.get('ACCESS_PASSWORD', '123456')
    
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
        
    # 初始化登录尝试次数，防止暴力破解
    if "login_attempts" not in st.session_state:
        st.session_state.login_attempts = 0

    if not st.session_state.authenticated:
        # 如果尝试次数太多，直接锁定页面
        if st.session_state.login_attempts >= 5:
            st.error("尝试次数过多，为了安全起见，已锁定登录。请重启服务或稍后再试。")
            return False

        st.warning("请输入访问密码以继续。")
        pwd = st.text_input("密码", type="password")
        if st.button("登录"):
            if pwd == CORRECT_PASSWORD:
                st.session_state.authenticated = True
                st.session_state.login_attempts = 0 # 登录成功清零
                st.rerun()
            else:
                st.session_state.login_attempts += 1
                # 故意增加延迟（时间惩罚），抵御脚本暴力破解
                time.sleep(2)
                st.error(f"密码错误！剩余尝试次数: {5 - st.session_state.login_attempts}")
        return False
    return True

# 如果未通过验证，则停止向下执行后续的聊天代码
if not check_password():
    st.stop()

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
            load_chat(item["path"])
            st.rerun()

def format_latex(text):
    r"""提取公共方法：将模型可能输出的 \( 和 \[ 替换为受支持的 $ 和 $$"""
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
    return text

# 显示历史对话记录 (跳过系统提示词)
for msg in st.session_state.messages:
    if msg["role"] != "system":
        with st.chat_message(msg["role"]):
            st.markdown(format_latex(msg["content"]))

# 接收用户输入
if prompt := st.chat_input("请输入文本"):
    
    # 1. 把用户的问题展示在网页上
    with st.chat_message("user"):
        st.markdown(prompt)
    
    # 2. 把用户的问题追加到历史记录中
    st.session_state.messages.append({"role": "user", "content": prompt})
    # 这里不需要立刻存硬盘，忍住！
    
    # 3. 请求大模型并展示回答（这里为了体验更好，使用流式输出 stream=True）
    with st.chat_message("assistant"):
        # ★ 关键修复：只使用一个 st.empty() 占位符，避免两个占位符交替更新
        # 导致 Streamlit 前端 DOM 重绘时影响已渲染的历史消息 CSS（文本发白现象）
        response_placeholder = st.empty()
        
        full_response = ""
        full_thinking = ""
        last_update_time = 0  # 使用时间戳控制刷新频率，彻底解决长文本越往后越卡的现象
        
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
            # 请求 API
            response = client.chat.completions.create(**api_kwargs)
            
            # 逐字渲染结果
            for chunk in response:
                delta = chunk.choices[0].delta
                current_time = time.time()
                
                # 兼容处理返回的思考过程内容
                reasoning = getattr(delta, 'reasoning_content', None)
                if reasoning:
                    full_thinking += reasoning
                
                if delta.content is not None:
                    full_response += delta.content
                
                # 使用单一占位符统一渲染，消除 DOM 抖动
                if current_time - last_update_time > 0.08:
                    # 根据当前状态构建完整的 Markdown 内容
                    parts = []
                    if full_thinking:
                        if not full_response:
                            # 还在思考阶段：显示思考过程 + 闪烁光标（灰色，与完成后统一）
                            thinking_html = (
                                f'<div style="background:rgba(128,128,128,0.1);border-left:4px solid rgba(128,128,128,0.35);'
                                f'padding:10px 14px;border-radius:4px;margin-bottom:8px;'
                                f'font-size:0.92em;line-height:1.6;">'
                                f'🤔 <strong>思考中...</strong><br>{full_thinking}▌</div>'
                            )
                            parts.append(thinking_html)
                        else:
                            # 已有回复：显示完整思考过程（可折叠）+ 回复内容 + 光标
                            thinking_html = (
                                f'<div style="background:rgba(128,128,128,0.1);border-left:4px solid rgba(128,128,128,0.35);'
                                f'padding:8px 14px;border-radius:4px;margin-bottom:10px;'
                                f'font-size:0.9em;line-height:1.6;">'
                                f'<details open><summary>🤔 <strong>思考过程</strong></summary>'
                                f'{full_thinking}</details></div>'
                            )
                            parts.append(thinking_html)
                            parts.append(format_latex(full_response) + "▌")
                    else:
                        # 无思考模式：直接显示回复
                        parts.append(format_latex(full_response) + "▌")
                    
                    response_placeholder.markdown("\n\n".join(parts), unsafe_allow_html=True)
                    last_update_time = current_time
                    
            # 渲染结束，进行最后一次完整显示（去掉光标），确保不漏掉最后的字符
            parts = []
            if full_thinking:
                thinking_html = (
                    f'<div style="background:rgba(128,128,128,0.1);border-left:4px solid rgba(128,128,128,0.35);'
                    f'padding:8px 14px;border-radius:4px;margin-bottom:10px;'
                    f'font-size:0.9em;line-height:1.6;">'
                    f'<details open><summary>🤔 <strong>思考过程</strong></summary>'
                    f'{full_thinking}</details></div>'
                )
                parts.append(thinking_html)
            parts.append(format_latex(full_response))
            response_placeholder.markdown("\n\n".join(parts), unsafe_allow_html=True)
            
        except Exception as e:
            st.error(f"API 请求失败: {e}")
            # 剔除刚才写入历史记录的用户提问，保证状态回退允许用户重试
            st.session_state.messages.pop()
            st.stop()
        
    # 4. 把 AI 的回答追加到历史记录中
    st.session_state.messages.append({"role": "assistant", "content": full_response})
    
    # 【性能优化关键】：AI 答复完之后，才合并进行一次“集中的保存”，绝不打断打字流
    save_current_chat() 
    get_history_chats.clear() # 清空侧边栏列表缓存，告知最新的一条有更新
