import os
import time
import streamlit as st
from openai import OpenAI
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

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

# 定义一个初始化或重置对话的函数
def init_or_reset_chat():
    st.session_state.messages = [
        {"role": "system", "content": "You are a helpful assistant. Please strictly use `$` for inline math and `$$` for block math formulas."}
    ]

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
if "messages" not in st.session_state:
    init_or_reset_chat()

# 在侧边栏添加新建对话按钮
with st.sidebar:
    st.title("功能菜单")
    if st.button("➕ 新建对话", use_container_width=True, type="primary"):
        init_or_reset_chat()
        st.rerun()

# 显示历史对话记录 (跳过系统提示词)
for msg in st.session_state.messages:
    if msg["role"] != "system":
        with st.chat_message(msg["role"]):
            # 将模型可能输出的 \( 和 \[ 替换为受支持的 $ 和 $$，同时避免破坏 \\[ 这种 LaTeX 换行符
            import re
            display_text = msg["content"]
            display_text = re.sub(r'(?<!\\)\\\[', '$$', display_text)
            display_text = re.sub(r'(?<!\\)\\\]', '$$', display_text)
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
    
    # 3. 请求大模型并展示回答（这里为了体验更好，使用流式输出 stream=True）
    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        full_response = ""
        
        # 请求 API
        response = client.chat.completions.create(
            model="deepseek-v4-pro",
            messages=st.session_state.messages,
            stream=True, # 开启流式输出，打字机效果
            # 注: stream 模式下如果开启 thinking，目前 SDK 解析可能有所不同，这里先去掉 extra_body 保持基本对话畅通
        )
        
        # 逐字渲染结果
        for chunk in response:
            if chunk.choices[0].delta.content is not None:
                full_response += chunk.choices[0].delta.content
                
                import re
                display_text = full_response
                display_text = re.sub(r'(?<!\\)\\\[', '$$', display_text)
                display_text = re.sub(r'(?<!\\)\\\]', '$$', display_text)
                display_text = re.sub(r'(?<!\\)\\\(', '$', display_text)
                display_text = re.sub(r'(?<!\\)\\\)', '$', display_text)
                
                # st.markdown 原生支持 LaTeX ($...$ 和 $$...$$)，会自动渲染出来
                message_placeholder.markdown(display_text + "▌")
                
        # 移除光标，完整显示
        display_text = full_response
        display_text = re.sub(r'(?<!\\)\\\[', '$$', display_text)
        display_text = re.sub(r'(?<!\\)\\\]', '$$', display_text)
        display_text = re.sub(r'(?<!\\)\\\(', '$', display_text)
        display_text = re.sub(r'(?<!\\)\\\)', '$', display_text)
        message_placeholder.markdown(display_text)
        
    # 4. 把 AI 的回答追加到历史记录中
    st.session_state.messages.append({"role": "assistant", "content": full_response})
