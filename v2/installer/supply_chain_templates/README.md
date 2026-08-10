# Windows runtime 供应链材料模板

发布用 `runtime-root` 必须由受控构建机从 Python 官方 64 位 embeddable ZIP
重新组装，并在 `supply-chain/` 中携带以下四份已填写材料：

- `runtime-provenance.json`：CPython 来源 URL、上游 ZIP SHA-256、版本、架构，
  以及另外三份材料的 SHA-256；
- `requirements.lock`：所有 Python 包的固定版本与下载制品 SHA-256；
- `sbom.cdx.json`：CycloneDX SBOM，至少列出 Python、Flask、waitress；
- `THIRD-PARTY-NOTICES.txt`：实际随包分发组件的许可证与版权说明。

正式材料由 `v2.installer.build_runtime` 根据官方 CPython ZIP、经哈希锁定的
wheelhouse 和 `v2/backend/requirements-win-amd64.lock` 自动生成。本目录文件只是
不可直接发布的格式骨架和人工复核提示。构建器会拒绝占位哈希、版本不符、
`python313._pth` 路径不符、非 AMD64 PE、材料哈希不一致或缺失许可证说明的
runtime。实际许可证全文应从对应上游源码/发行包取得并由发布负责人复核。

正式 `python313._pth` 必须且只能包含：

```text
python313.zip
.
Lib\site-packages
..\app
```

不得启用 `import site`，不得加入裸 `..`，不得携带 `pyvenv.cfg`。
