import os
import re
import asyncio
import logging
import logging.config
from enum import Enum, auto

import httpx
import chainlit as cl
from openai import AsyncOpenAI
from dotenv import load_dotenv
from typing import Any

# 设置日志记录器（logger 引用在模块级获取是安全的，真正的配置在 on_app_startup 中完成）
logger = logging.getLogger(__name__)

# 加载环境变量（必须在模块顶层，因为后续全局变量依赖环境变量初始化）
load_dotenv()

# --- 数据持久化层与初始化配置 ---
async def _init_db_schema_async() -> None:
    pass

# 如果没有配置官方的 PostgreSQL DATABASE_URL，则使用本地 SQLite 数据库
if not os.getenv("DATABASE_URL"):
    import aiosqlite
    from chainlit.data.sql_alchemy import SQLAlchemyDataLayer

    _sqlite_initialized = False
    _sqlite_init_lock = None

    async def _init_db_schema_async() -> None:
        """使用异步方式懒加载数据库表，消除全局作用域的同步阻塞"""
        global _sqlite_initialized, _sqlite_init_lock
        if _sqlite_initialized:
            return
        
        # 惰性初始化 Lock，确保在事件循环创建后实例化
        if _sqlite_init_lock is None:
            _sqlite_init_lock = asyncio.Lock()
            
        async with _sqlite_init_lock:
            if _sqlite_initialized:
                return
            
            async with aiosqlite.connect("chainlit.db") as conn:
                # 优化：开启 WAL (Write-Ahead Logging) 模式，极大提升 SQLite 异步读写并发性能
                await conn.execute("PRAGMA journal_mode=WAL;")
                await conn.execute("PRAGMA synchronous=NORMAL;")
                
                await conn.execute("""CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    identifier TEXT UNIQUE NOT NULL,
                    createdAt TEXT NOT NULL,
                    metadata TEXT
                )""")
                
                await conn.execute("""CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY,
                    createdAt TEXT,
                    name TEXT,
                    userId TEXT,
                    userIdentifier TEXT,
                    tags TEXT,
                    metadata TEXT,
                    FOREIGN KEY (userId) REFERENCES users(id)
                )""")
                
                await conn.execute("""CREATE TABLE IF NOT EXISTS steps (
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
                
                await conn.execute("""CREATE TABLE IF NOT EXISTS feedbacks (
                    id TEXT PRIMARY KEY,
                    forId TEXT NOT NULL,
                    threadId TEXT NOT NULL,
                    value INTEGER NOT NULL,
                    comment TEXT,
                    FOREIGN KEY (forId) REFERENCES steps(id),
                    FOREIGN KEY (threadId) REFERENCES threads(id)
                )""")
                
                await conn.execute("""CREATE TABLE IF NOT EXISTS elements (
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
                
                # --- steps 表迁移 ---
                async with conn.execute("PRAGMA table_info(steps)") as cursor:
                    rows = await cursor.fetchall()
                    existing_columns = [row[1] for row in rows]
                    
                for col, col_def in [
                    ("command", "TEXT"),                         
                    ("defaultOpen", "INTEGER NOT NULL DEFAULT 1"),
                    ("autoCollapse", "INTEGER NOT NULL DEFAULT 0"),
                    ("modes", "TEXT"),                           
                    ("disableFeedback", "INTEGER NOT NULL DEFAULT 0"),
                    ("indent", "INTEGER"),
                ]:
                    if col not in existing_columns:
                        await conn.execute(f"ALTER TABLE steps ADD COLUMN {col} {col_def}")
                
                # --- feedbacks 表迁移代码 ---
                async with conn.execute("PRAGMA table_info(feedbacks)") as cursor:
                    rows = await cursor.fetchall()
                    existing_fb_columns = [row[1] for row in rows]
                    
                if "threadId" not in existing_fb_columns:
                    await conn.execute("ALTER TABLE feedbacks ADD COLUMN threadId TEXT")
                
                await conn.commit()
            _sqlite_initialized = True

    # 使用 @cl.data_layer 装饰器注册数据层（必须用装饰器，直接赋值无效）
    @cl.data_layer
    def get_data_layer():
        return SQLAlchemyDataLayer(conninfo="sqlite+aiosqlite:///chainlit.db")

# --- 密码认证（历史会话功能的前提） ---
@cl.password_auth_callback
async def auth_callback(username: str, password: str) -> cl.User | None:
    await _init_db_schema_async()
    # 在此处可对接自己的用户数据库，以下为演示账号
    if username == "admin" and password == "admin":
        return cl.User(identifier="admin", metadata={"role": "admin"})
    return None

# --- 应用级生命周期：日志配置 ---
@cl.on_app_startup
async def setup_logging() -> None:
    """在 Chainlit 应用启动时配置日志系统。

    使用 dictConfig 而非 basicConfig，原因：
    1. dictConfig 会覆盖已有配置（basicConfig 在已有 handler 时是空操作）
    2. Chainlit 2.9.2+ 已声明不全局设置 logging，我们的配置是唯一来源
    3. disable_existing_loggers=False 确保第三方库（httpx、openai）的 logger 不受影响
    """
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s - %(levelname)s - %(name)s - %(message)s",
            },
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "default",
            },
        },
        "root": {
            "level": "INFO",
            "handlers": ["default"],
        },
    })
    logger.info("Logging configured successfully")


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
    base_url="https://api.deepseek.com",
    max_retries=3,          # 优化：增加重试机制应对大模型偶发性服务器 502/网关拥塞
    # read 超时设为 120 秒：reasoning_effort="max" 时模型可能思考 30~60 秒才产生首个 token
    timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
) if api_key else None

# 如需使用 Prompt Playground 调试，可取消此行注释以追踪 OpenAI 调用
# if openai_client:
#     cl.instrument_openai()


async def persist_settings(settings: dict[str, Any]) -> None:
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
        logger.warning("Failed to persist settings", exc_info=True)  # 持久化失败不影响主流程


# 优化：预编译正则表达式对象，显著提升 `resume_chat` 恢复大量历史步骤时的处理速度
THINKING_PATTERN = re.compile(r'<details[^>]*>.*?</details>\s*', flags=re.DOTALL)


def strip_thinking(content: str) -> str:
    """从消息内容中移除 <details> 思考块，返回纯净的回复文本。
    用于从数据库恢复会话时，过滤掉思考 HTML 标记，只保留实际回复内容。"""
    cleaned = THINKING_PATTERN.sub('', content)
    return cleaned.strip()


class StreamState(Enum):
    """流式响应的状态机。取代原先 msg_sent / thinking_started / thinking_closed 三个布尔值。"""
    IDLE = auto()       # 尚未发送任何内容
    THINKING = auto()   # 正在输出 <details> 思考过程
    ANSWERING = auto()  # 思考已闭合，正在输出正式回复

@cl.on_chat_start
async def start_chat():
    await _init_db_schema_async()
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
    await _init_db_schema_async()
    if not openai_client:
        await cl.Message(content="⚠️ 未检测到环境变量 `DEEPSEEK_API_KEY`，请检查 .env 文件。").send()
        return

    # user_session 已由 Chainlit 自动恢复，直接读取
    settings = cl.user_session.get("settings") or DEFAULT_SETTINGS
    role_name = settings.get("role", "均衡默认")
    system_prompt = ROLES.get(role_name, ROLES["均衡默认"])
    cl.user_session.set("system_prompt", system_prompt)

    # 重新发送设置面板（否则切换历史对话后配置按钮会消失）
    role_list = list(ROLES.keys())
    model_list = ["deepseek-v4-pro", "deepseek-v4-flash"]
    reasoning_list = ["high", "max"]

    role_idx = role_list.index(settings["role"]) if settings["role"] in role_list else 0
    model_idx = model_list.index(settings["model"]) if settings["model"] in model_list else 0
    reasoning_idx = reasoning_list.index(settings["reasoning_effort"]) if settings.get("reasoning_effort") in reasoning_list else 0

    await cl.ChatSettings(
        [
            cl.input_widget.Select(
                id="role",
                label="选择助手角色",
                values=role_list,
                initial_index=role_idx,
            ),
            cl.input_widget.Select(
                id="model",
                label="选择模型",
                values=model_list,
                initial_index=model_idx,
            ),
            cl.input_widget.Switch(
                id="enable_thinking",
                label="开启思考模式",
                initial=settings.get("enable_thinking", True),
            ),
            cl.input_widget.Select(
                id="reasoning_effort",
                label="选择思考强度",
                values=reasoning_list,
                initial_index=reasoning_idx,
            ),
        ]
    ).send()

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
async def setup_agent(settings: dict[str, Any]) -> None:
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
        # 由于是引用修改，无需再次 cl.user_session.set("message_history", message_history)

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
    state = StreamState.IDLE

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
                if state == StreamState.IDLE:
                    # 首个思考 token：先写入 <details> 开头再发送消息
                    msg.content = '<details open class="thinking-details">\n<summary>🤔 思考过程</summary>\n\n'
                    await msg.send()
                    state = StreamState.THINKING
                await msg.stream_token(reasoning)

            # 处理普通回复内容
            if delta.content:
                if state == StreamState.THINKING:
                    # 思考结束 → 闭合 <details>，后续为正式回复
                    await msg.stream_token('\n</details>\n\n')
                    state = StreamState.ANSWERING
                if state == StreamState.IDLE:
                    await msg.send()
                    state = StreamState.ANSWERING
                await msg.stream_token(delta.content)

        # 确保 <details> 闭合（仅有思考、无实际回复内容时）
        if state == StreamState.THINKING:
            await msg.stream_token('\n</details>')

        if state != StreamState.IDLE:
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
        # 由于 message_history 是内存引用，前面 append 已经生效，这里无需再 cl.user_session.set

    except asyncio.CancelledError:
        # 处理用户主动打断（点击停止生成）将抛出 CancelledError 异常
        if state == StreamState.THINKING:
            await msg.stream_token('\n</details>\n\n> ⚠️ *已在此处停止思考并中断生成*\n')
        else:
            await msg.stream_token('\n\n> ⚠️ *回答生成已中断*\n')

        if state != StreamState.IDLE:
            await msg.update()
        # 由于 message_history 是内存引用，中断时不追加相当于未修改对象，同样无需 cl.user_session.set

    except Exception as e:
        if current_content and message_history and message_history[-1]["role"] == "user":
            message_history.pop()  # 撤销因为出错而未回复的用户消息
        await cl.Message(content=f"⚠️ API 请求失败: {str(e)}").send()
        