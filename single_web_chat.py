import os
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

# 初始化对话历史，存放在 Streamlit 的 session_state 中
if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "system", "content": "You are a helpful assistant. Please strictly use `$` for inline math and `$$` for block math formulas."}
    ]

# 显示历史对话记录 (跳过系统提示词)
for msg in st.session_state.messages:
    if msg["role"] != "system":
        with st.chat_message(msg["role"]):
            # 将模型可能输出的 \( 和 \[ 替换为受支持的 $ 和 $$
            display_text = msg["content"].replace("\\[", "$$").replace("\\]", "$$").replace("\\(", "$").replace("\\)", "$")
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
                
                # 将大模型常见的 \( 和 \[ 格式实时替换为 Streamlit 标准的 $ 和 $$
                display_text = full_response.replace("\\[", "$$").replace("\\]", "$$").replace("\\(", "$").replace("\\)", "$")
                
                # st.markdown 原生支持 LaTeX ($...$ 和 $$...$$)，会自动渲染出来
                message_placeholder.markdown(display_text + "▌")
                
        # 移除光标，完整显示
        display_text = full_response.replace("\\[", "$$").replace("\\]", "$$").replace("\\(", "$").replace("\\)", "$")
        message_placeholder.markdown(display_text)
        
    # 4. 把 AI 的回答追加到历史记录中
    st.session_state.messages.append({"role": "assistant", "content": full_response})
