# Spine Magic Builder

Spine Magic Builder 是一个以 Windows 为主的重建工具包，用于处理散乱或命名不良的 Spine 资源。它以字节级扫描目录树，识别骨骼（skeleton）和 atlas，匹配纹理页，并将它们重建为规范化的 Spine 集合，便于可视化验证与进一步处理。

本项目面向已提取出的资源目录树，在这些树中文件名和扩展名可能不可信。它支持 JSON 和二进制骨骼发现，支持内嵌或独立的 atlas 数据，以及 PNG/JPEG/WebP 等常见图像格式。

> 仅对您拥有或被授权检查的资源使用此工具。项目不包含任何游戏资产、Spine 运行时或查看器二进制文件。

## 快速开始

### 要求

- Windows 10 或 11
- Python 3.10 或更新版本，可通过 `py` 或 `python` 在 `PATH` 上访问
- 可选：用于可视化验证的 [SpineViewer](https://github.com/ww-rm/SpineViewer)
- 可选 GUI 增强：Pillow 与 tkinterdnd2

克隆或下载本仓库，然后（可选）安装 GUI 附加依赖：

```powershell
py -3 -m pip install -r requirements-optional.txt
```

核心构建器仅使用 Python 标准库。Pillow 提供更广泛的图像尺寸和缩略图支持；tkinterdnd2 为 GUI 添加拖放功能。

### 推荐工作流

1. 将源文件夹拖到 `Run_SpineMagic_Builder_Candidate_Stage_v3.bat`。
2. 等待构建器在邻近位置创建 `Spine_Built-*` 目录。
3. 打开 `Run_SpineCandidatePicker_GUI.bat`。
4. 浏览到包含 `_candidates` 文件夹的已构建目录。
5. 选择页面与候选项，然后按 **Activate** 或按数字键。
6. 在提示时指向 `SpineViewer.exe` 并以视觉方式验证集合。
7. 标记选择为正确、加入黑名单或跳过该页面。

GUI 会在下列位置记录查看器路径与决策：

```text
%LOCALAPPDATA%\SpineMagicBuilder\spine_candidate_picker_state.json
```

可以通过设置 `SPINE_MAGIC_BUILDER_STATE` 使用不同的状态文件位置。设置 `SPINE_VIEWER_EXE` 可定义初始查看器路径。

## 包含的程序

| 文件 | 用途 |
| --- | --- |
| `spine_magic_builder.py` | 核心的递归扫描器与规范化集合构建器。 |
| `spine_magic_builder_candidate_materializer_v3.py` | 扩展构建器：候选分阶段与单候选物化。 |
| `spine_candidate_picker_gui.py` | 用于审阅、激活和记录候选选择的 Tk GUI。 |
| `Run_SpineMagic_Builder.bat` | 保守的拷贝模式构建器预设。 |
| `Run_SpineMagic_Builder_Candidate_Stage_v3.bat` | 针对大型提取树优化的候选分阶段预设。 |
| `Run_SpineCandidatePicker_GUI.bat` | GUI 启动器；可接受可选的起始文件夹。 |

## 输出组织方式

除非在命令行显式提供 `--move`，否则源文件会保留在原处。随附的启动器从不使用 `--move`。

对于选定的源文件夹，输出会写到其旁边的生成容器中，类似于：

```text
ParentFolder\
├── SourceFolder\
└── Spine_Built-Context_Name\
    └── SourceFolder\
        └── normalized_spine_set\
            ├── normalized_spine_set.skel (或 .json)
            ├── normalized_spine_set.atlas
            ├── 纹理页
            └── _candidates\
```

候选激活会在放置新候选之前，将先前活动的页面保存在 `_materialized_history` 中。

## 启动器细节

### 标准构建器

`Run_SpineMagic_Builder.bat` 使用拷贝模式和适中的 atlas 匹配阈值。将文件夹拖到它上面或调用：

```bat
Run_SpineMagic_Builder.bat "D:\ExtractedGame\assets"
```

### 候选阶段构建器

`Run_SpineMagic_Builder_Candidate_Stage_v3.bat` 会将直接子文件夹视为实体，对具有相同尺寸但含糊的纹理进行排序，并对所有候选进行分阶段处理。它会请求创建符号链接以避免重复数据（当系统支持时）。

在直接从 Python 调用时，如果无限制分阶段会产生过多候选，可以使用 `--stage-dim-candidates-limit N` 来限制。

### 候选挑选 GUI

无参数运行并浏览到某个文件夹，或提供起始路径：

```bat
Run_SpineCandidatePicker_GUI.bat "D:\ExtractedGame\Spine_Built-Example"
```

常用按键：

| 键 | 操作 |
| --- | --- |
| `1`-`9` | 激活候选排名 1-9 |
| `Enter` | 激活所选候选 |
| `Left` / `Right` | 上一 / 下一候选 |
| `Ctrl+Left` / `Ctrl+Right` | 上一 / 下一 atlas 页面 |
| `C` | 标记所选候选为正确 |
| `B` | 将所选候选加入黑名单 |
| `S` | 跳过当前页面 |
| `F5` | 启动 SpineViewer |

如果未自动找到 SpineViewer，可使用 **Viewer exe** 按钮。自动搜索会检查仓库文件夹、`SpineViewer` 子文件夹以及仓库的父文件夹。

## 命令行使用

核心构建器：

```powershell
py -3 spine_magic_builder.py --root "D:\ExtractedGame\assets" --dims-fallback --prefer-nearby-textures
```

分阶段同维度模糊匹配：

```powershell
py -3 spine_magic_builder_candidate_materializer_v3.py `
  --root "D:\ExtractedGame\assets" `
  --dims-fallback `
  --stage-dim-candidates `
  --stage-dim-candidates-limit 250
```

在没有 GUI 的情况下激活一个已分阶段的候选：

```powershell
py -3 spine_magic_builder_candidate_materializer_v3.py `
  --materialize-built-set "D:\Path\To\OneBuiltSet" `
  --materialize-page 1 `
  --materialize-candidate 3 `
  --link-mode copy
```

运行任一构建器并加 `--help` 查看完整选项列表。常用高级开关包括 `--explain-match`、`--top-n`、`--entity-mode`、`--rewrite-pages-to-match-source` 和 `--dedupe-tex...`（原文中有更多选项）。

## 安全说明

- 默认 CLI 行为会复制源纹理；标准启动器显式使用拷贝模式。
- 随附的启动器从不传递 `--move`。
- 符号链接与硬链接模式可以节省磁盘空间，但会将生成的输出与原始纹理数据关联；如果要编辑已构建的集合，请使用拷贝模式。
- `--aggressive-atlas` 与 `--dims-fallback` 能提高从损坏命名中恢复的能力，但可能产生错误匹配。对于模糊的结果，请以视觉方式验证。
- 当无法使用链接时，候选分阶段可能会占用大量磁盘空间；如有需要，请设置有限的候选限制。
- 生成的状态、游戏资产、候选文件夹与构建输出已被 `.gitignore` 排除。

## 故障排除

**未找到 Python**  
安装 Python 3.10+ 并在安装程序中启用将 Python 添加到 `PATH` 的选项，然后打开新的终端。

**GUI 打开但缩略图受限**  
安装 `requirements-optional.txt` 中的依赖。若缺少 Pillow，GUI 会回退到 Tk 的内置 PNG 加载器。

**拖放不起作用**  
安装 `tkinterdnd2`，或使用 GUI 的浏览按钮。

**在 Windows 上创建符号链接失败**  
启用 Windows 开发者模式、以合适权限运行，或使用 `--link-mode copy`。候选构建器已在无法创建符号链接时回退为硬链接，然后再回退到拷贝。

**找不到 SpineViewer**  
从上游的 [SpineViewer 仓库](https://github.com/ww-rm/SpineViewer) 下载一个发行版，然后使用 GUI 的 **Viewer exe** 按钮选择 `SpineViewer.exe`。

## 致谢

特别感谢 [ww-rm/SpineViewer](https://github.com/ww-rm/SpineViewer) 及其贡献者。SpineViewer 是 GUI 工作流中用于视觉验证的外部组件。

SpineViewer 未随本项目打包或重新许可。详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 许可

Spine Magic Builder 的原始代码与文档以 [MIT License](LICENSE) 许可发布。第三方程序与资源受其各自条款约束。
