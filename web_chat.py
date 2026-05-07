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
    "均衡默认": "You are a helpful assistant.",
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
                # 取前 15 个字符作为标题
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
    enable_thinking = st.toggle("开启思考模式", value=False)
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
    
    # 扫描并显示历史对话
    chat_files = glob.glob(os.path.join(CHATS_DIR, "*.json"))
    # 按文件名递减排序（最新的在最上面）
    chat_files.sort(reverse=True)
    
    for f in chat_files:
        try:
            with open(f, "r", encoding="utf-8") as file:
                data = json.load(file)
                title = data.get("title", "未命名对话")
                chat_id = data.get("id", "")
        except Exception:
            continue
        
        # 标识当前选中的对话
        is_current = (chat_id == st.session_state.get("current_chat_id"))
        label = f"🔵 {title}" if is_current else f"💬 {title}"
        
        # 当点击旧对话时，加载数据并刷新界面
        if st.button(label, key=f"btn_{chat_id}", use_container_width=True):
            load_chat(f)
            st.rerun()

# 显示历史对话记录 (跳过系统提示词)
for msg in st.session_state.messages:
    if msg["role"] != "system":
        with st.chat_message(msg["role"]):
            # 将模型可能输出的 \( 和 \[ 替换为受支持的 $ 和 $$，同时避免破坏 \\[ 这种 LaTeX 换行符
            import re
            display_text = msg["content"]
            display_text = re.sub(r'(?<!\\)\\\[', '\n$$\n', display_text)
            display_text = re.sub(r'(?<!\\)\\\]', '\n$$\n', display_text)
            display_text = re.sub(r'(?<!\\)\\\(', '$', display_text)
            display_text = re.sub(r'(?<!\\)\\\)', '$', display_text)
            st.markdown(display_text)

# 接收用户输入
if prompt := st.chat_input("请输入文本"):
    
    # 1. 把用户的问题展示在网页上
    with st.chat_message("user"):
        st.markdown(prompt)
    
    # 2. 把用户的问题追加到历史记录中
    st.session_state.messages.append({"role": "user", "content": prompt})
    save_current_chat() # 保存到本地
    
    # 3. 请求大模型并展示回答（这里为了体验更好，使用流式输出 stream=True）
    with st.chat_message("assistant"):
        thinking_placeholder = st.empty()
        message_placeholder = st.empty()
        
        full_response = ""
        full_thinking = ""
        
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
            
        # 请求 API
        response = client.chat.completions.create(**api_kwargs)
        
        # 逐字渲染结果
        for chunk in response:
            delta = chunk.choices[0].delta
            
            # 兼容处理返回的思考过程内容
            reasoning = getattr(delta, 'reasoning_content', None)
            if reasoning:
                full_thinking += reasoning
                # 使用 st.info 容器通过图标和背景色区别“思考过程”
                with thinking_placeholder.container():
                    st.info(full_thinking + "▌", icon="🤔")
            
            if delta.content is not None:
                # 当开始输出实际回复时，把思考过程的跳动光标移除
                if full_thinking and not full_response:
                    with thinking_placeholder.container():
                        st.info(full_thinking, icon="🤔")
                        
                full_response += delta.content
                
                import re
                display_text = full_response
                display_text = re.sub(r'(?<!\\)\\\[', '\n$$\n', display_text)
                display_text = re.sub(r'(?<!\\)\\\]', '\n$$\n', display_text)
                display_text = re.sub(r'(?<!\\)\\\(', '$', display_text)
                display_text = re.sub(r'(?<!\\)\\\)', '$', display_text)
                
                # st.markdown 原生支持 LaTeX ($...$ 和 $$...$$)，会自动渲染出来
                message_placeholder.markdown(display_text + "▌")
                
        # 移除光标，完整显示
        display_text = full_response
        display_text = re.sub(r'(?<!\\)\\\[', '\n$$\n', display_text)
        display_text = re.sub(r'(?<!\\)\\\]', '\n$$\n', display_text)
        display_text = re.sub(r'(?<!\\)\\\(', '$', display_text)
        display_text = re.sub(r'(?<!\\)\\\)', '$', display_text)
        message_placeholder.markdown(display_text)
        
    # 4. 把 AI 的回答追加到历史记录中
    st.session_state.messages.append({"role": "assistant", "content": full_response})
    save_current_chat() # 保存到本地
