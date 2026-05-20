import os
import json
import uuid
import chainlit as cl
from openai import AsyncOpenAI
from dotenv import load_dotenv
from typing import Optional

# 加载环境变量
load_dotenv()

# --- SQLite 数据持久化配置 ---
# 如果没有配置官方的 PostgreSQL DATABASE_URL，则使用本地 SQLite 数据库
if not os.getenv("DATABASE_URL"):
    import sqlite3
    from chainlit.data.sql_alchemy import SQLAlchemyDataLayer

    # Chainlit 的 SQLAlchemyDataLayer 使用原生 SQL 而非 ORM，
    # 因此需要在模块加载时手动创建所有必需的表。
    def _init_sqlite_schema():
        conn = sqlite3.connect("chainlit.db")
        c = conn.cursor()
        
        c.execute("""CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            identifier TEXT UNIQUE NOT NULL,
            createdAt TEXT NOT NULL,
            metadata TEXT
        )""")
        
        c.execute("""CREATE TABLE IF NOT EXISTS threads (
            id TEXT PRIMARY KEY,
            createdAt TEXT,
            name TEXT,
            userId TEXT,
            userIdentifier TEXT,
            tags TEXT,
            metadata TEXT,
            FOREIGN KEY (userId) REFERENCES users(id)
        )""")
        
        c.execute("""CREATE TABLE IF NOT EXISTS steps (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            threadId TEXT NOT NULL,
            parentId TEXT,
            streaming INTEGER NOT NULL DEFAULT 0,
            waitForAnswer INTEGER,
            isError INTEGER,
            metadata TEXT,
            tags TEXT,
            input TEXT,
            output TEXT,
            createdAt TEXT,
            start TEXT,
            end TEXT,
            generation TEXT,
            showInput TEXT,
            language TEXT,
            defaultOpen INTEGER NOT NULL DEFAULT 1,
            autoCollapse INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (threadId) REFERENCES threads(id)
        )""")
        
        # Chainlit 2.11.x 新增列，如果表已存在则补充
        for col, col_def in [
            ("defaultOpen", "INTEGER NOT NULL DEFAULT 1"),
            ("autoCollapse", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            try:
                c.execute(f"ALTER TABLE steps ADD COLUMN {col} {col_def}")
            except sqlite3.OperationalError:
                pass  # 列已存在
        
        c.execute("""CREATE TABLE IF NOT EXISTS feedbacks (
            id TEXT PRIMARY KEY,
            forId TEXT NOT NULL,
            value INTEGER NOT NULL,
            comment TEXT,
            FOREIGN KEY (forId) REFERENCES steps(id)
        )""")
        
        c.execute("""CREATE TABLE IF NOT EXISTS elements (
            id TEXT PRIMARY KEY,
            threadId TEXT NOT NULL,
            type TEXT NOT NULL,
            chainlitKey TEXT,
            url TEXT,
            objectKey TEXT,
            name TEXT NOT NULL,
            display TEXT NOT NULL,
            size TEXT,
            language TEXT,
            page INTEGER,
            autoPlay INTEGER,
            playerConfig TEXT,
            forId TEXT,
            mime TEXT,
            props TEXT,
            FOREIGN KEY (threadId) REFERENCES threads(id),
            FOREIGN KEY (forId) REFERENCES steps(id)
        )""")
        
        conn.commit()
        conn.close()
    
    _init_sqlite_schema()

    # 使用 @cl.data_layer 装饰器注册数据层（必须用装饰器，直接赋值无效）
    @cl.data_layer
    def get_data_layer():
        return SQLAlchemyDataLayer(conninfo="sqlite+aiosqlite:///chainlit.db")

# --- 密码认证（历史会话功能的前提） ---
@cl.password_auth_callback
def auth_callback(username: str, password: str) -> Optional[cl.User]:
    # 在此处可对接自己的用户数据库，以下为演示账号
    if username == "admin" and password == "admin":
        return cl.User(identifier="admin", metadata={"role": "admin"})
    return None

# 定义系统提示词角色
ROLES = {
    "均衡默认": "你是一个乐于助人的AI智能助手。请根据用户的输入自然、准确、友善地作答。\n- 请保持回答清晰、简洁、逻辑分明。\n- 遇到不知道或不确定的知识，请客观坦诚地告知，不编造虚假信息。",
    "编码大师": "你是一名世界顶级的首席软件工程师和架构师。你的目标是输出最高质量、符合生产环境标准的代码。请严格遵循以下原则：\n1. 优先提供优雅、高效、可维护且符合该语言最佳实践的代码。\n2. 提供的代码尽量完整且可以直接运行，避免使用含糊的伪代码。\n3. 在代码中添加精练的中文注释以解释复杂的核心逻辑。\n4. 主动思考并指出潜在的边界条件（Edge Cases）、异常处理和性能优化建议。\n5. 减少过多不必要的寒暄，直奔技术要点和解决方案。",
    "数学大师": "你是一位极其严谨的理论数学家与受人尊敬的教授。请以极致的逻辑性和专业性回答问题。请严格遵循以下要求：\n1. 必须使用准确的 LaTeX 表达数学概念，行内公式严格使用 `$` 包裹，独立块级公式严格使用 `$$` 包裹。\n2. 对于计算或证明题，必须采取分步解析（Step-by-Step）的方式，写出清晰的演算过程。\n3. 在得出结论后，尽可能简要总结其背后的核心定理或数学直觉。\n4. 保持语言的学术性与严谨性。"
}

@cl.on_chat_start
async def start_chat():
    api_key = os.environ.get('DEEPSEEK_API_KEY')
    if not api_key:
        await cl.Message(content="⚠️ 未检测到环境变量 `DEEPSEEK_API_KEY`，请检查 .env 文件。").send()
        return

    # 初始化 OpenAI 客户端
    client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com"
    )
    cl.user_session.set("client", client)

    # 发送系统设置面板
    settings = await cl.ChatSettings(
        [
            cl.input_widget.Select(
                id="role",
                label="选择助手角色",
                values=list(ROLES.keys()),
                initial_index=0,
            ),
            cl.input_widget.Select(
                id="model",
                label="选择模型",
                values=["deepseek-v4-pro", "deepseek-v4-flash"], 
                initial_index=0,
            ),
            cl.input_widget.Switch(
                id="enable_thinking",
                label="开启思考模式",
                initial=True,
            ),
            cl.input_widget.Select(
                id="reasoning_effort",
                label="选择思考强度",
                values=["high", "max"],
                initial_index=0,
            ),
        ]
    ).send()

    cl.user_session.set("settings", settings)
    
    # 初始化历史消息
    role_name = settings["role"] if settings else "均衡默认"
    cl.user_session.set("messages", [{"role": "system", "content": ROLES.get(role_name, ROLES["均衡默认"])}])


@cl.on_chat_resume
async def resume_chat(thread: cl.types.ThreadDict):
    """恢复历史会话 — 当用户在侧边栏点击历史对话时触发"""
    api_key = os.environ.get('DEEPSEEK_API_KEY')
    if not api_key:
        await cl.Message(content="⚠️ 未检测到环境变量 `DEEPSEEK_API_KEY`，请检查 .env 文件。").send()
        return

    # 初始化 OpenAI 客户端
    client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com"
    )
    cl.user_session.set("client", client)

    # 尝试从 thread metadata 恢复角色
    role_name = "均衡默认"
    thread_metadata = thread.get("metadata") or {}
    # metadata 可能是 JSON 字符串，需要解析
    if isinstance(thread_metadata, str):
        try:
            thread_metadata = json.loads(thread_metadata)
        except json.JSONDecodeError:
            thread_metadata = {}
    if "role" in thread_metadata:
        role_name = thread_metadata["role"]

    # 重建消息历史（从持久化的 steps 中提取对话记录）
    messages = [{"role": "system", "content": ROLES.get(role_name, ROLES["均衡默认"])}]
    
    for step in thread.get("steps", []):
        # 确保 step 是字典类型
        if not isinstance(step, dict):
            continue
        if step.get("parentId") is None:
            step_type = step.get("type")
            if step_type == "user_message":
                # 用户消息内容在 input 字段中
                content = step.get("input", "") or step.get("output", "")
            elif step_type == "assistant_message":
                # 助手回复内容在 output 字段中
                content = step.get("output", "")
            else:
                continue
            if content and isinstance(content, str):
                role = "user" if step_type == "user_message" else "assistant"
                messages.append({"role": role, "content": content})
    
    cl.user_session.set("messages", messages)

    # 发送系统设置面板（恢复会话时也允许调整设置）
    settings = await cl.ChatSettings(
        [
            cl.input_widget.Select(
                id="role",
                label="选择助手角色",
                values=list(ROLES.keys()),
                initial_index=list(ROLES.keys()).index(role_name) if role_name in ROLES else 0,
            ),
            cl.input_widget.Select(
                id="model",
                label="选择模型",
                values=["deepseek-v4-pro", "deepseek-v4-flash"],
                initial_index=0,
            ),
            cl.input_widget.Switch(
                id="enable_thinking",
                label="开启思考模式",
                initial=True,
            ),
            cl.input_widget.Select(
                id="reasoning_effort",
                label="选择思考强度",
                values=["high", "max"],
                initial_index=0,
            ),
        ]
    ).send()

    cl.user_session.set("settings", settings)


@cl.on_settings_update
async def setup_agent(settings):
    cl.user_session.set("settings", settings)
    role_name = settings["role"]
    
    # 更新系统提示词
    messages = cl.user_session.get("messages", [])
    if messages and messages[0]["role"] == "system":
        messages[0]["content"] = ROLES.get(role_name, ROLES["均衡默认"])
    else:
        messages.insert(0, {"role": "system", "content": ROLES.get(role_name, ROLES["均衡默认"])})
    cl.user_session.set("messages", messages)

@cl.on_message
async def main(message: cl.Message):
    client = cl.user_session.get("client")
    settings = cl.user_session.get("settings")
    messages = cl.user_session.get("messages")

    if not client:
        await cl.Message(content="⚠️ 客户端未初始化，请刷新页面重试。").send()
        return

    # 更新用户消息
    messages.append({"role": "user", "content": message.content})

    model = settings["model"] if settings else "deepseek-v4-pro"
    enable_thinking = settings["enable_thinking"] if settings else True
    reasoning_effort = settings["reasoning_effort"] if settings else "high"

    msg = cl.Message(content="")
    await msg.send()
    
    thinking_step = None
    if enable_thinking:
        thinking_step = cl.Step(name="🤔 思考过程")
        await thinking_step.send()

    full_response = ""
    full_thinking = ""

    # 准备 API 请求参数
    api_kwargs = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    if enable_thinking:
        api_kwargs["reasoning_effort"] = reasoning_effort
        api_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
    else:
        api_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

    try:
        # 请求大模型流式响应
        stream = await client.chat.completions.create(**api_kwargs)

        async for chunk in stream:
            delta = chunk.choices[0].delta
            
            # 处理思考过程 (reasoning_content 专属于 deepseek-reasoner 等支持思考的模型)
            reasoning = getattr(delta, 'reasoning_content', None)
            if reasoning and thinking_step:
                full_thinking += reasoning
                await thinking_step.stream_token(reasoning)
                
            # 处理普通回复内容
            if delta.content:
                full_response += delta.content
                await msg.stream_token(delta.content)

        if thinking_step:
            await thinking_step.update()
            
        await msg.update()

        # 将助手回复加入历史记录
        messages.append({"role": "assistant", "content": full_response})
        cl.user_session.set("messages", messages)

    except Exception as e:
        await cl.Message(content=f"⚠️ API 请求失败: {str(e)}").send()
        messages.pop() # 请求失败时移除最后一条用户消息，方便重试
