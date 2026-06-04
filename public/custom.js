/**
 * Chainlit 自定义快捷键脚本
 * Ctrl+K (Windows/Linux) 或 Cmd+K (Mac) → 新建对话（SPA 导航，不刷新页面）
 *
 * 实现原理：
 *   Chainlit 的"新建对话"按钮（id="new-chat-button"）内部调用
 *   clear() + React Router navigate('/')，是纯 SPA 导航。
 *   本脚本通过点击该按钮触发 React 事件处理器，并在弹出确认对话框时
 *   自动点击确认按钮，确保行为与手动点击完全一致。
 */

(function () {
  'use strict';

  /**
   * 触发新建对话 — 模拟点击 Chainlit 原生按钮
   *
   * 步骤：
   *   1. 查找 id="new-chat-button"（Chainlit 源码固定 ID）
   *   2. 点击按钮 → 触发 React onClick
   *      （已配置 confirm_new_chat=false，无需确认，直接 SPA 导航）
   */
  function triggerNewChat() {
    // 查找新建对话按钮（Chainlit 固定 ID，最可靠）
    const newChatBtn = document.querySelector('#new-chat-button');
    if (!newChatBtn) {
      console.warn('[快捷键] 未找到 #new-chat-button，请检查 Chainlit 版本');
      return;
    }

    // 点击按钮，触发 React 的 handleClickOpen
    // confirm_new_chat=false → 直接执行 clear() + navigate('/')
    newChatBtn.click();
    console.log('[快捷键] Ctrl+K → 已新建对话');
  }

  /**
   * 键盘事件处理
   */
  function handleKeydown(event) {
    // Ctrl+K (Windows/Linux) 或 Cmd+K (Mac)
    if ((event.ctrlKey || event.metaKey) && event.key === 'k') {
      event.preventDefault();
      event.stopPropagation();
      triggerNewChat();
    }
  }

  // 使用捕获阶段注册，确保在 Chainlit 自身的 Ctrl+K（搜索框）之前拦截
  document.addEventListener('keydown', handleKeydown, true);
  console.log('[快捷键] Ctrl+K 新建对话快捷键已就绪');
})();
