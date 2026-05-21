/**
 * Chainlit 自定义快捷键脚本
 * Ctrl+K (Windows/Linux) 或 Cmd+K (Mac) → 新建对话
 */

(function () {
  'use strict';

  /**
   * 在 DOM 中查找"新建对话"按钮
   * Chainlit 的 UI 结构可能会随版本变化，这里尝试多种选择器以提高兼容性
   */
  function findNewChatButton() {
    // 1. 通过 aria-label 查找（Chainlit 2.x 常用）
    const ariaSelectors = [
      '[aria-label="New chat"]',
      '[aria-label="new chat"]',
      '[aria-label="新建对话"]',
      '[aria-label="新对话"]',
    ];
    for (const sel of ariaSelectors) {
      const el = document.querySelector(sel);
      if (el) return el;
    }

    // 2. 遍历所有 button 和 a 标签，匹配文本内容
    const candidates = document.querySelectorAll('button, a, [role="button"]');
    const matchTexts = ['new chat', '新建对话', '新对话', 'new conversation'];
    for (const el of candidates) {
      const text = (el.textContent || '').toLowerCase().trim();
      for (const match of matchTexts) {
        if (text === match || text.includes(match)) {
          return el;
        }
      }
    }

    // 3. 查找侧边栏中带有 + 或编辑图标的按钮（常见于新建按钮）
    const iconButtons = document.querySelectorAll('button');
    for (const btn of iconButtons) {
      // Chainlit 新建对话按钮通常在侧边栏顶部
      const svg = btn.querySelector('svg');
      if (svg && btn.closest('aside, nav, [class*="sidebar"], [class*="Sidebar"]')) {
        const ariaLabel = btn.getAttribute('aria-label') || '';
        if (ariaLabel.toLowerCase().includes('new') || ariaLabel.includes('新建')) {
          return btn;
        }
      }
    }

    return null;
  }

  /**
   * 触发新建对话
   */
  function triggerNewChat() {
    const btn = findNewChatButton();
    if (btn) {
      btn.click();
      console.log('[快捷键] Ctrl+K → 已触发新建对话');
    } else {
      // 降级方案：直接导航到根路径（Chainlit 默认行为会新建对话）
      console.log('[快捷键] Ctrl+K → 未找到按钮，尝试导航到 /');
      window.location.href = '/';
    }
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

  // 注册事件监听
  document.addEventListener('keydown', handleKeydown, true);
  console.log('[快捷键] Ctrl+K 新建对话快捷键已就绪');
})();
