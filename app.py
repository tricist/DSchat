import os
import re
import asyncio
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
            command TEXT,
            defaultOpen INTEGER NOT NULL DEFAULT 1,
            autoCollapse INTEGER NOT NULL DEFAULT 0,
            disableFeedback INTEGER NOT NULL DEFAULT 0,
            indent INTEGER,
            modes TEXT,
            FOREIGN KEY (threadId) REFERENCES threads(id)
        )""")
        
        c.execute("""CREATE TABLE IF NOT EXISTS feedbacks (
            id TEXT PRIMARY KEY,
            forId TEXT NOT NULL,
            threadId TEXT NOT NULL,
            value INTEGER NOT NULL,
            comment TEXT,
            FOREIGN KEY (forId) REFERENCES steps(id),
            FOREIGN KEY (threadId) REFERENCES threads(id)
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
        
        # Chainlit 各版本新增列的严谨迁移机制
        # 使用 PRAGMA table_info 校验，避免 try-except 吞掉真实的 OperationalError
        # 迁移必须在 CREATE TABLE 之后执行，确保表已存在
        
        # --- steps 表迁移 ---
        c.execute("PRAGMA table_info(steps)")
        existing_columns = [row[1] for row in c.fetchall()]
        for col, col_def in [
            ("command", "TEXT"),                         # Chainlit 2.1.0+
            ("defaultOpen", "INTEGER NOT NULL DEFAULT 1"),# Chainlit 2.3.0+
            ("autoCollapse", "INTEGER NOT NULL DEFAULT 0"),# Chainlit 2.11.x+
            ("modes", "TEXT"),                           # Chainlit 2.9.4+
            ("disableFeedback", "INTEGER NOT NULL DEFAULT 0"),
            ("indent", "INTEGER"),
        ]:
            if col not in existing_columns:
                c.execute(f"ALTER TABLE steps ADD COLUMN {col} {col_def}")
        
        # --- feedbacks 表迁移（官方 schema 包含 threadId，旧表可能缺失） ---
        c.execute("PRAGMA table_info(feedbacks)")
        existing_fb_columns = [row[1] for row in c.fetchall()]
        if "threadId" not in existing_fb_columns:
            c.execute("ALTER TABLE feedbacks ADD COLUMN threadId TEXT")
        
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
    "均衡默认": "你是乐于助人的AI助手。回答需清晰、简洁、逻辑分明。不知道的内容坦诚告知，不编造虚假信息。",
    "编码大师": "你是顶级软件工程师。输出高质量、可直接运行的代码，遵循最佳实践，添加精练注释，指出边界条件和性能优化建议。直奔技术要点，避免寒暄。",
    "数学大师": "你是严谨的数学家。用 LaTeX 表达数学：`$` 包裹行内公式，`$$` 包裹块级公式。`$$` 必须独占一行、前后换行，结束后空一行再写后续内容，禁止 `$` 与 `$$` 互相嵌套。分步演算，总结核心定理。"
}

# 默认设置
DEFAULT_SETTINGS = {
    "role": "均衡默认",
    "model": "deepseek-v4-pro",
    "enable_thinking": True,
    "reasoning_effort": "high",
}

# --- 全局 API 客户端 ---
# AsyncOpenAI 原生协程安全并自带连接池，直接全局实例化即可
api_key = os.environ.get('DEEPSEEK_API_KEY')
openai_client = AsyncOpenAI(
    api_key=api_key,
    base_url="https://api.deepseek.com"
) if api_key else None

# 如需使用 Prompt Playground 调试，可取消此行注释以追踪 OpenAI 调用
# if openai_client:
#     cl.instrument_openai()


async def persist_settings(settings: dict):
    """将当前设置持久化到线程 metadata 中。"""
    try:
        from chainlit.data import get_data_layer
        dl = get_data_layer()
        if dl and hasattr(dl, 'update_thread'):
            thread_id = cl.context.session.thread_id
            if thread_id:
                # 嵌套保存在 "settings" 键下。
                # 避免与 Chainlit 自动序列化的 user_session 在更新时产生浅拷贝合并冲突。
                await dl.update_thread(thread_id=thread_id, metadata={"settings": settings})
    except Exception:
        pass  # 持久化失败不影响主流程


def strip_thinking(content: str) -> str:
    """从消息内容中移除 <details> 思考块，返回纯净的回复文本。
    用于从数据库恢复会话时，过滤掉思考 HTML 标记，只保留实际回复内容。"""
    cleaned = re.sub(r'<details[^>]*>.*?</details>\s*', '', content, flags=re.DOTALL)
    return cleaned.strip()

@cl.on_chat_start
async def start_chat():
    if not openai_client:
        await cl.Message(content="⚠️ 未检测到环境变量 `DEEPSEEK_API_KEY`，请检查 .env 文件。").send()
        return

    # 发送系统设置面板（.send() 返回设置值的 dict，非 ChatSettings 对象）
    settings_values = await cl.ChatSettings(
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

    cl.user_session.set("settings", settings_values)
    # 持久化设置到线程 metadata
    await persist_settings(settings_values)
    
    # 初始化系统提示词
    role_name = settings_values["role"] if settings_values else "均衡默认"
    system_prompt = ROLES.get(role_name, ROLES["均衡默认"])
    cl.user_session.set("system_prompt", system_prompt)
    # 初始化对话历史（仅含系统消息，后续每轮在 main 中基于内存追加，无需反复读库）
    cl.user_session.set("message_history", [{"role": "system", "content": system_prompt}])


@cl.on_chat_resume
async def resume_chat(thread: cl.types.ThreadDict):
    """恢复历史会话 — 当用户在侧边栏点击历史对话时触发

    直接从入参 thread 中提取步骤，同步重构对话历史。
    """
    if not openai_client:
        await cl.Message(content="⚠️ 未检测到环境变量 `DEEPSEEK_API_KEY`，请检查 .env 文件。").send()
        return

    # user_session 已由 Chainlit 自动恢复，直接读取
    settings = cl.user_session.get("settings") or DEFAULT_SETTINGS
    role_name = settings.get("role", "均衡默认")
    system_prompt = ROLES.get(role_name, ROLES["均衡默认"])
    cl.user_session.set("system_prompt", system_prompt)
    
    # 直接从入参 thread 重建 message_history
    message_history = [{"role": "system", "content": system_prompt}]
    for step in thread.get("steps", []):
        if not isinstance(step, dict):
            continue
        step_type = step.get("type")
        if step_type == "user_message":
            content = step.get("input", "") or step.get("output", "")
            if content and isinstance(content, str):
                message_history.append({"role": "user", "content": content})
        elif step_type == "assistant_message":
            content = step.get("output", "")
            if content and isinstance(content, str):
                content = strip_thinking(content)
                if content:
                    message_history.append({"role": "assistant", "content": content})
                    
    # 保存重建后的历史到 session
    cl.user_session.set("message_history", message_history)


@cl.on_settings_update
async def setup_agent(settings):
    cl.user_session.set("settings", settings)
    # 持久化设置到线程 metadata
    await persist_settings(settings)
    # 更新系统提示词，同步更新对话历史中的 system 消息
    role_name = settings["role"]
    system_prompt = ROLES.get(role_name, ROLES["均衡默认"])
    cl.user_session.set("system_prompt", system_prompt)
    # 同步更新对话历史中的系统消息（新的角色设定对后续对话生效）
    message_history = cl.user_session.get("message_history")
    if message_history and message_history[0]["role"] == "system":
        message_history[0]["content"] = system_prompt
        cl.user_session.set("message_history", message_history)

@cl.on_message
async def main(message: cl.Message):
    client = openai_client
    settings = cl.user_session.get("settings")
    system_prompt = cl.user_session.get("system_prompt")

    if not client:
        await cl.Message(content="⚠️ 客户端未初始化，请刷新页面重试。").send()
        return

    model = settings["model"] if settings else "deepseek-v4-pro"
    enable_thinking = settings["enable_thinking"] if settings else True
    reasoning_effort = settings["reasoning_effort"] if settings else "high"

    # 获取对话历史：新会话在 start_chat 初始化，恢复会话在 resume_chat 初始化
    message_history = cl.user_session.get("message_history")
    if message_history is None:
        # Fallback，仅针对异常分支
        message_history = [{"role": "system", "content": system_prompt or ROLES["均衡默认"]}]
        cl.user_session.set("message_history", message_history)
    # 追加当前用户消息
    current_content = message.content or ""
    if current_content:
        message_history.append({"role": "user", "content": current_content})

    # 使用单个 Message，思考过程用 <details> 包裹在消息内部
    # 这样每个对话轮次只产生一个 assistant step，确保数据库持久化与恢复正确
    msg = cl.Message(content="")
    msg_sent = False
    thinking_started = False  # 是否已有实际思考内容流式输出
    thinking_closed = False   # </details> 是否已闭合
    full_response = ""

    # 准备 API 请求参数
    api_kwargs = {
        "model": model,
        "messages": message_history,
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

            # 处理思考过程
            reasoning = getattr(delta, 'reasoning_content', None)
            if reasoning:
                if not msg_sent:
                    # 首个思考 token：先写入 <details> 开头再发送消息
                    msg.content = '<details open class="thinking-details">\n<summary>🤔 思考过程</summary>\n\n'
                    await msg.send()
                    msg_sent = True
                    thinking_started = True
                await msg.stream_token(reasoning)

            # 处理普通回复内容
            if delta.content:
                if thinking_started and not thinking_closed:
                    # 思考结束 → 闭合 <details>，后续为正式回复
                    await msg.stream_token('\n</details>\n\n')
                    thinking_closed = True
                if not msg_sent:
                    await msg.send()
                    msg_sent = True
                full_response += delta.content
                await msg.stream_token(delta.content)

        # 确保 <details> 闭合（仅有思考、无实际回复内容时）
        if thinking_started and not thinking_closed:
            await msg.stream_token('\n</details>')

        if msg_sent:
            await msg.update()
            # 将助手回复（去除思考 HTML 块）追加到对话历史
            clean_content = strip_thinking(msg.content)
            if clean_content:
                message_history.append({"role": "assistant", "content": clean_content})
        else:
            # 既无思考也无回复（极端情况）
            msg.content = "（模型未返回任何内容）"
            await msg.send()
            await msg.update()
        # 持久化对话历史到用户会话
        cl.user_session.set("message_history", message_history)

    except asyncio.CancelledError:
        # 处理用户主动打断（点击停止生成）将抛出 CancelledError 异常
        if thinking_started and not thinking_closed:
            await msg.stream_token('\n</details>\n\n> ⚠️ *已在此处停止思考并中断生成*\n')
            thinking_closed = True
        else:
            await msg.stream_token('\n\n> ⚠️ *回答生成已中断*\n')
        
        if msg_sent:
            await msg.update()
        # 中断时不追加助手回复到历史（仅保留用户消息，下一轮可继续）
        cl.user_session.set("message_history", message_history)

    except Exception as e:
        await cl.Message(content=f"⚠️ API 请求失败: {str(e)}").send()
