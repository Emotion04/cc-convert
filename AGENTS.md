# 开发指南

## 配置文件

- 位置: `~/.ccconvert/config.toml`（Linux/macOS）或 `%USERPROFILE%\.ccconvert\config.toml`（Windows）
- 首次运行自动创建，编辑添加 API Key

## 发布流程

1. 更新版本号（pyproject.toml, `__init__.py`, main.py）
2. 运行测试: `pytest`
3. 提交并推送代码
4. 创建并推送 tag 触发 GitHub Actions 发布:
   ```bash
   git tag vx.y.z
   git push origin vx.y.z
   ```

## 提交规范

- 新代码测试覆盖率 > 90%
- 总体覆盖率 ≥ 80%
- 运行 `superpowers:verification-before-completion` 验证通过后再提交

## 桌面 UI 规范（硬规则）

- `/desktop` 前端必须使用 vendored 的 universal-ui（`web/static/desktop/vendor/universal-ui/`，
  完整运行时已内置）：组件清单见 `docs/universal-ui/components.md`，集成契约见
  `docs/universal-ui/integration.md`。**禁止手写第二个按钮/对话框/toast/菜单**，
  禁止写死色值与 magic number（间距/颜色一律用 `--ui-space-*` / `--ui-*-色` token）。
- 本机装有 universal-ui 技能（`universal-ui` skill）；写桌面前端前先读清单再动手。
- 业务逻辑只写在 Manager API；壳（`desktop-shell/`）零业务。详见 ADR-014 与不变量 J 组。
