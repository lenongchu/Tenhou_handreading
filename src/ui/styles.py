"""
GUI 静态资源与样式

抽离自 gui_app.py 的样式表与长文本常量。
"""


def _get_app_stylesheet() -> str:
    """科技感深色主题样式表"""
    return """
    /* === 全局 === */
    QWidget { background-color: #0d1117; color: #c9d1d9; }
    QMainWindow { background-color: #0d1117; }
    
    /* === GroupBox 卡片区 === */
    QGroupBox {
        font-weight: bold;
        font-size: 11pt;
        color: #58a6ff;
        border: 1px solid #30363d;
        border-radius: 8px;
        margin-top: 12px;
        padding: 12px 12px 8px 12px;
        background-color: #161b22;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 12px;
        top: 2px;
        padding: 0 6px;
        color: #00d4ff;
        background-color: #161b22;
    }
    
    /* === 按钮 === */
    QPushButton {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #21262d, stop:1 #161b22);
        color: #c9d1d9;
        border: 1px solid #30363d;
        border-radius: 6px;
        padding: 6px 14px;
        font-weight: 500;
    }
    QPushButton:hover {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #30363d, stop:1 #21262d);
        border-color: #58a6ff;
        color: #fff;
    }
    QPushButton:pressed {
        background-color: #0d1117;
    }
    QPushButton:disabled {
        background-color: #21262d;
        color: #484f58;
        border-color: #21262d;
    }
    
    /* 主操作按钮 === */
    QPushButton#query_btn {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #238636, stop:1 #2ea043);
        color: #fff;
        border: 1px solid #2ea043;
        font-size: 13pt;
        padding: 10px 20px;
    }
    QPushButton#query_btn:hover {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #2ea043, stop:1 #3fb950);
    }
    QPushButton#query_btn:disabled {
        background: #21262d;
        color: #484f58;
        border-color: #21262d;
    }
    
    /* 小按钮 ? === */
    QPushButton[text="?"] {
        padding: 2px 8px;
        font-size: 10pt;
    }
    
    /* === 输入框 === */
    QLineEdit, QTextEdit, QPlainTextEdit, QTextBrowser {
        background-color: #0d1117;
        color: #c9d1d9;
        border: 1px solid #30363d;
        border-radius: 6px;
        padding: 6px 10px;
        selection-background-color: #388bfd;
    }
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {
        border-color: #58a6ff;
    }
    
    /* === 下拉框 === */
    QComboBox {
        background-color: #0d1117;
        color: #c9d1d9;
        border: 1px solid #30363d;
        border-radius: 6px;
        padding: 6px 12px;
        min-height: 20px;
    }
    QComboBox:hover { border-color: #58a6ff; }
    QComboBox::drop-down { border: none; }
    QComboBox QAbstractItemView {
        background-color: #161b22;
        color: #c9d1d9;
        selection-background-color: #388bfd;
    }
    
    /* === 数字框 === */
    QSpinBox {
        background-color: #0d1117;
        color: #c9d1d9;
        border: 1px solid #30363d;
        border-radius: 6px;
        padding: 4px 8px;
    }
    QSpinBox:focus { border-color: #58a6ff; }
    
    /* === 复选框 / 单选框 === */
    QCheckBox, QRadioButton {
        color: #c9d1d9;
        spacing: 8px;
    }
    QCheckBox::indicator, QRadioButton::indicator {
        width: 16px;
        height: 16px;
        border: 2px solid #30363d;
        border-radius: 3px;
        background-color: #0d1117;
    }
    QRadioButton::indicator { border-radius: 8px; }
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {
        background-color: #58a6ff;
        border-color: #58a6ff;
    }
    QCheckBox:hover::indicator, QRadioButton:hover::indicator {
        border-color: #58a6ff;
    }
    
    /* === 进度条 === */
    QProgressBar {
        border: 1px solid #30363d;
        border-radius: 4px;
        text-align: center;
        background-color: #0d1117;
    }
    QProgressBar::chunk {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 #00d4ff, stop:1 #58a6ff);
        border-radius: 3px;
    }
    
    /* === 标签 === */
    QLabel { color: #c9d1d9; }
    QLabel[colorHint="muted"] { color: #8b949e; }
    
    /* === 列表 === */
    QListWidget {
        background-color: #0d1117;
        color: #c9d1d9;
        border: 1px solid #30363d;
        border-radius: 6px;
    }
    QListWidget::item:selected { background-color: #388bfd; }
    
    /* === 滚动区域 === */
    QScrollArea { border: none; background: transparent; }
    QScrollBar:vertical {
        background: #0d1117;
        width: 10px;
        border-radius: 5px;
        margin: 0;
    }
    QScrollBar::handle:vertical {
        background: #30363d;
        border-radius: 5px;
        min-height: 30px;
    }
    QScrollBar::handle:vertical:hover { background: #484f58; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    
    /* === 分割器 === */
    QSplitter::handle { background-color: #30363d; width: 2px; }
    
    /* === Tab / 对话框 === */
    QDialog { background-color: #0d1117; }
    QTabWidget::pane {
        border: 1px solid #30363d;
        border-radius: 6px;
        background-color: #161b22;
        margin-top: 0;
        padding: 12px;
        top: 2px;
    }
    QTabBar {
        background: transparent;
        border: none;
    }
    QTabBar::tab {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #21262d, stop:1 #161b22);
        color: #8b949e;
        border: 1px solid #30363d;
        border-radius: 6px;
        padding: 6px 16px;
        margin-right: 6px;
        font-weight: 500;
        min-width: 64px;
    }
    QTabBar::tab:selected {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #1a5fb4, stop:1 #388bfd);
        color: #fff;
        border-color: #58a6ff;
    }
    QTabBar::tab:hover:!selected {
        color: #c9d1d9;
        border-color: #58a6ff;
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #30363d, stop:1 #21262d);
    }
    QTabBar::tab:first {
        margin-left: 0;
    }
    """


PATTERN_HELP_HTML = """
<h2>舍牌模式输入说明</h2>
<p>用 <b>-</b> 或 <b>AND</b> 分隔各张牌，按出牌顺序从左到右书写。</p>

<h3>一、数牌</h3>
<ul>
<li><b>1m～9m</b> 萬子、<b>1p～9p</b> 筒子、<b>1s～9s</b> 索子</li>
<li><b>赤五</b>：<code>0m</code> 赤5万、<code>0p</code> 赤5筒、<code>0s</code> 赤5索；参与花色变换，0m/0p/0s 三者与数牌一起等价（如 3m-2m-0m ≡ 7s-8s-0s）；含赤五时等价变体为 9+9 个</li>
<li><b>花色通配符</b>：<code>m</code> 任意万字、<code>p</code> 任意饼子、<code>s</code> 任意索子（<u>单字符</u>，与 <code>1m</code>、<code>2p</code>、<code>3s</code> 等两字符具体牌区分）</li>
<li>等价：1s-3s 与 1m-3m、1p-3p 等自动等价（花色对称，1 种花色 3 变体，2–3 种花色 6 变体）</li>
</ul>

<h3>二、摸切标记 t 与 f</h3>
<ul>
<li><b>t</b>：必须摸切；<b>f</b>：手切或摸切皆可；无后缀为必须手切</li>
<li>例：<code>3mt-1m</code> = 3m 摸切、1m 手切；<code>3mf-1m</code> = 3m 手摸切皆可、1m 手切</li>
</ul>

<h3>三、关联牌标记 k（仅数牌）</h3>
<ul>
<li>在<strong>数牌</strong>写法后加后缀 <b>k</b>，表示该张舍牌打出时须为<strong>关联牌</strong>（related tile）：当时手牌中，<u>同花色</u>须存在与打出牌数字差 ≤ 2 的<strong>搭子</strong>（两面/边张/嵌张）或<strong>对子</strong>。字牌、花色通配符 <code>m</code>/<code>p</code>/<code>s</code> 等不支持 <code>k</code>。</li>
<li><b>k 写在最后</b>，可与 <b>t</b>（摸切）、<b>f</b>（手摸切皆可）、<b>r</b>（立直宣言）组合，例如 <code>2pk</code>（手切关联）、<code>3mtk</code>（摸切且关联）、<code>4mfk</code>（手摸切皆可且关联）、<code>5mrk</code> / <code>5mtrk</code>（立直宣言且关联，依书写为手切/摸切立直）。</li>
<li><b>数字范围</b>：<code>[25]mk</code> = 2m～5m 其一且为关联牌（默认手切）；<code>[29]mf</code>、<code>[29]mt</code> 等同理，范围表达式末尾只能选一个修饰字母（不能写 <code>[25]mfk</code> 这种双后缀，需改用 OR 等方式）。</li>
<li>与「分析目标 → <b>关联牌判断</b>」的区别：<b>舍牌模式里的 <code>k</code></b>约束的是<strong>模式中该张</strong>打出时必须满足关联；<b>关联牌判断</b>是在整段模式匹配成功后，再按<strong>目标牌</strong>在序列里最后一次出现的那一打，判断是否关联。二者可同时使用。</li>
</ul>

<h3>四、立直宣言牌 r</h3>
<ul>
<li>牌后加 <b>r</b> 表示该牌为立直宣言牌（打出此牌宣告立直）</li>
<li>例：<code>3m-5mr</code> = 玩家先打 3m，再打 5m 时宣告立直；统计立直瞬间手牌中目标牌的数量</li>
<li>立直宣言牌必为摸切；立直后该玩家舍牌均为摸切</li>
<li><b>互斥</b>：r 与吃(c)、碰(p) 不可同现，立直玩家不可副露</li>
<li>含 r 时优先检索牌谱是否有玩家立直，无则快速跳过</li>
</ul>

<h3>五、通配符 * 与 $</h3>
<ul>
<li><b>*</b> 表示"任意摸切"，可匹配中间任意张摸切牌</li>
<li>例：<code>3m-*-1m</code> = 3m 与 1m 之间允许若干摸切，不允许手切</li>
<li><b>$</b> 表示"任意一张手切"（吃/碰之后的牌必为手切）</li>
<li>例：<code>c0p6p-$</code> = 用 0p6p 吃后打出任意一张手切牌</li>
</ul>

<h3>六、拆搭 cd1 / cd2 / cdm / cdp / cds</h3>
<p>搭子 = 两张同花色、数值差 1 或 2 的数牌（如 1m3m、2m3m、4s5s）。<b>1s9s 不是搭子</b>。拆搭须为手切，且另一张也须为手切。</p>
<ul>
<li><b>cd1</b>：任意拆搭（万/筒/索均可）</li>
<li><b>cd2</b>：拆搭，且拆搭花色 ≠ 下一张舍牌花色。例：<code>cd2-1m</code> 拆的不能是万</li>
<li><b>cdm</b> / <b>cdp</b> / <b>cds</b>：拆万字搭 / 饼搭 / 索搭</li>
<li>例：<code>cd1-3m</code> 先拆某搭子，再打 3m；<code>cd2-5s</code> 拆非索搭后打 5s</li>
</ul>

<h3>七、吃 / 碰占位</h3>
<p>在舍牌序列中表示"此处有一次吃或碰"，不占一张舍牌；<b>语义为具体吃的/碰的牌</b>，如 <code>4mc3m5m</code> 表示必须是用 3m5m 吃的 4m。</p>
<ul>
<li><b>吃</b>：任意 <code>(牌)c(牌)(牌)</code> 或 <code>c(牌)(牌)</code>，如 <code>1sc2s3s</code>、<code>4mc3m5m</code>、<code>c5m6m</code></li>
<li><b>碰</b>：<code>p1z1z</code> 用两个东碰（只碰东）；<code>pkfkf</code> 用客风碰</li>
<li><b>等价变体</b>：含吃或数牌碰时<u>不</u>生成花色等价变体；仅碰字牌时仍可生成变体</li>
</ul>

<h3>八、字牌</h3>
<p><b>1z～7z</b> 对应：东、南、西、北、白、发、中</p>

<h3>九、逻辑符号 NOT / OR / AND / [xy] 范围</h3>
<ul>
<li><b>[xy] 数字范围</b>：<code>[25]m</code> = 2m、3m、4m、5m 其一；<code>[17]z</code> = 1z～7z 其一；可与花色 m/p/s/z 搭配，参与等价变换</li>
<li><b>NOT[xy] 排除范围</b>：<code>NOT[45]m</code> = 除 4m、5m 外任意牌；<code>NOT[12]z</code> = 除东、南外任意牌</li>
<li><b>NOT 字牌</b>：<code>zNOT1z</code> 表示任意字牌但排除东；<code>zNOT1zNOT2z</code> 排除东、南</li>
<li><b>NOT 花色</b>：<code>NOTm</code> 任意一张非万字（筒/索/字均可）；<code>NOTp</code> 非饼；<code>NOTs</code> 非索</li>
<li><b>OR</b>：<code>3mOR5m</code> 表示该位置为 3m 或 5m 其一；<code>3mOR[25]m</code> 等价 3m 或 2m～5m 其一</li>
<li><b>AND</b>：与 <b>-</b> 同级，作舍牌顺序分隔符。例：<code>3mAND4m-zNOT1z</code> = 第一张 3m、第二张 4m、第三张任意字牌（非东）</li>
</ul>

<h3>十、字牌占位符</h3>
<table border="1" cellpadding="4" cellspacing="0" style="border-collapse:collapse;">
<tr><th>符号</th><th>含义</th></tr>
<tr><td><code>z</code></td><td>任意字牌（手切）</td></tr>
<tr><td><code>zt</code></td><td>任意字牌且摸切</td></tr>
<tr><td><code>zf</code></td><td>自风（当前局座风）</td></tr>
<tr><td><code>kf</code></td><td>任意一张客风</td></tr>
<tr><td><code>z1</code> <code>z2</code> <code>z3</code></td><td>互不相同的字牌</td></tr>
<tr><td><code>kf1</code> <code>kf2</code> <code>kf3</code></td><td>互不相同的客风</td></tr>
<tr><td><code>ap</code></td><td>安牌（满足其一即可，不含本张：该字牌可见1-3枚；或非场风、非三元字牌可见0张）</td></tr>
<tr><td><code>apr</code></td><td>立直宣言的安牌（该舍牌为立直宣言且满足安牌条件）</td></tr>
</table>

<h3>十一、示例</h3>
<ul>
<li><code>7s-9s</code>：相邻打出 7s、9s</li>
<li><code>7s-0m-9s</code>：7s、赤5万、9s</li>
<li><code>7s-4mc3m5m-9s</code>：7s、必须是用 3m5m 吃的 4m、9s（不含等价变体）</li>
<li><code>3m-z</code>：3m 后打任意字牌（6 种等价）</li>
<li><code>zt-zt</code>：连续两张摸切字牌（49 种等价）</li>
<li><code>z1-z2</code>：两张不同字牌（42 种等价）</li>
<li><code>3m-*-1m</code>：3m 与 1m 之间可隔若干摸切</li>
<li><code>zf-kf</code>：先打自风，再打客风</li>
<li><code>c0p6p-$</code>：用 0p6p 吃后打出任意一张手切牌</li>
<li><code>3m-5mr</code>：打 3m 后，打 5m 时立直（统计立直瞬间手牌目标数）</li>
<li><code>cd1-3m</code>：先拆某搭子（如 1m3m、4p5p），再打 3m</li>
<li><code>cd2-5s</code>：拆非索搭后打 5s</li>
<li><code>3m-NOTm-5m</code>：3m、任意非万字、5m</li>
<li><code>[25]m-6m</code>：2m～5m 其一，接着 6m（等价变换适用）</li>
<li><code>NOT[45]m-8s</code>：除 4m、5m 外任意一张，接着 8s</li>
<li><code>3mAND4m-zNOT1z</code>：3m、4m，第三张任意字牌但非东</li>
<li><code>3mOR5m-z</code>：第一张为 3m 或 5m，第二张任意字牌</li>
<li><code>[28]mf-6m</code>：2m～8m 其一（手摸切皆可），接着 6m；<code>mf</code>/<code>pf</code>/<code>sf</code> 同理</li>
<li><code>2pk-1m</code>：先打关联的 2p（手切），再打 1m</li>
<li><code>3mtk-z</code>：3m 摸切且为关联牌，接着任意字牌</li>
<li><code>[25]mk-6m</code>：2m～5m 其一且该打为关联牌，接着 6m</li>
</ul>

<h3>十二、前段禁打约束</h3>
<p>在约束区域「前段禁打」中输入模式，表示<u>舍牌模式开始前</u>（即匹配到的舍牌手顺之前）该玩家不能打出这些牌。支持与舍牌模式相同的语法，并与主模式同步等价变换。</p>
<ul>
<li><code>NOTm</code>：前段不能打出任何万字；<code>NOTmf</code> 同义且手摸切皆可；变体 1p-2p 时自动变为 NOTp</li>
<li><code>4mOR2m</code>：前段不能打出 4m 或 2m；用 OR 组合多牌</li>
<li>例：舍牌 <code>1m-3m</code> 目标 2m，前段 <code>NOTm</code> → 在 1m-3m 的手顺之前不能打过任何万字</li>
</ul>

<h3>十三、前段有打约束</h3>
<p>在约束区域「前段有打」中输入舍牌模式，表示<u>舍牌模式开始前</u>（即匹配到的主舍牌手顺之前）该玩家<u>必须出现过</u>这一段舍牌。语法与主舍牌模式完全相同；与主模式串联为「前段有打 … 主舍牌模式」，中间发生了什么不要求。</p>
<ul>
<li><code>[29]m-3pf</code>：前段曾按顺序打出过 2m 或 9m，再打出 3m（手摸切皆可）；等价变体时同步映射</li>
<li>例：主模式 <code>1s-3s</code> 目标 2s，前段有打 <code>[29]s-3sf</code> → 在 1s-3s 之前曾出现过 2s/9s 再 3s 的序列</li>
</ul>
"""


TARGET_HELP_HTML = """
<h2>目标牌输入说明</h2>
<p>统计舍牌序列匹配时，手牌中<u>持有该目标牌</u>的数量分布（目标牌存量、听牌、关联牌、搭子组合等均通过本语法解析）。</p>
<p><b>舍牌模式里的关联牌后缀 <code>k</code></b>与目标牌输入无关：若要在<strong>舍牌手顺</strong>中规定「这一张打出时必须是关联牌」，请在<strong>模式</strong>里对该数牌加后缀 <code>k</code>（如 <code>2pk</code>、<code>3mtk</code>、<code>[25]mk</code>）。完整说明见主界面「舍牌模式」旁 <b>?</b> 帮助中的「三、关联牌标记 k」。</p>

<h3>一、单张</h3>
<ul>
<li><b>6s</b>、<b>2m</b>、<b>东</b>、<b>1z</b> 等：统计该牌 0/1/2/3 张的概率</li>
<li>字牌可用中文或 1z～7z（东=1z、南=2z、西=3z、北=4z、白=5z、发=6z、中=7z）</li>
<li>默认情况下 <b>5p</b> 与 <b>0p</b>（赤五）在统计上<u>分别计数</u>；若希望「五这一位」把普通五与赤五视为同一目标，请使用下文 <b>五与赤五通配</b>。</li>
</ul>

<h3>二、五与赤五通配（<code>.</code>）</h3>
<p>在数字与花色之间插入英文句点 <code>.</code>，表示该张目标在统计时同时接受<strong>普通五</strong>与<strong>赤五（0m/0p/0s）</strong>，二者任一枚在手即满足该「一位」。</p>
<ul>
<li><b>5.p</b>、<b>5.m</b>、<b>5.s</b>：该张 = 5p 或 0p / 5m 或 0m / 5s 或 0s</li>
<li><b>简写</b> <b>45.p</b>：等价于 <b>4p</b> 与 <b>5.p</b> 组合（先要有 4p，且「五」位为普通五或赤五）</li>
<li><b>显式</b> <b>4p-5.p</b> 或 <b>4p5.p</b>（无连字符亦可解析）：与上相同</li>
<li><b>4.p</b> 等（首位非 5）：无赤牌可对应，等价于单张 <b>4p</b>，与写 <b>4p</b> 相同</li>
<li>与舍牌模式一致，目标牌参与花色等价变换时，<b>5.p</b> 会变为 <b>5.m</b>、<b>5.s</b> 等变体</li>
<li><b>即时铳率分析</b>（铳率页）：不支持 <code>.</code> 通配；请写 <b>5p</b> 或 <b>0p</b> 等明确牌。combo 搭子目标仍请用逗号多目标（如 <b>4s,5s</b>）</li>
</ul>

<h3>三、搭子（多张组合）</h3>
<ul>
<li><b>1m3m</b> 或 <b>1m-3m</b>：统计手牌是否<u>同时</u>有 1m 和 3m（各至少 1 张）</li>
<li><b>13m</b>：简写，等价 1m3m</li>
<li>搭子模式结果只有两种：没有 / 有</li>
<li>搭子中可含 <b>5.p</b> 等，例如 <b>45.p</b> 表示 4p 与「5 或赤五」同时满足</li>
<li>主界面可勾选 <b>独立性筛选</b>（仅「目标牌存量」生效）：在手牌其余部分尽量拆除顺子/刻子与字刻后，若存在一种拆法使该两枚搭子不能与其余牌组成完整面子（顺/刻），才计为「有」；可减少长连如 <code>45678m</code> 对中间搭子的重复计数</li>
</ul>

<h3>四、多目标（同时分析多张牌）</h3>
<ul>
<li><b>6s 2m 5p</b> 或 <b>6s,2m,5p</b>：用空格或逗号分隔，一次分析同时统计 6s、2m、5p 各 0/1/2/3 张的分布</li>
<li>一次匹配、一次遍历，无需多次查询</li>
<li>生成样本时可选择为哪个目标牌筛选</li>
</ul>

<h3>五、示例</h3>
<ul>
<li>舍牌 <code>7s-9s</code> 目标 <code>8s</code>：听 8s 时，手牌有 0/1/2/3 张 8s 的概率</li>
<li>舍牌 <code>1s-3s</code> 目标 <code>2s</code>：听 2s 时，手牌有 2s 的概率</li>
<li>舍牌 <code>1m-3m</code> 目标 <code>1m3m</code>：已有 1m3m 搭子时，手牌是否握有该搭子</li>
<li>舍牌 <code>7s-9s</code> 目标 <code>6s 8s</code>：同时统计 6s 和 8s 的存量分布</li>
<li>目标 <b>45.p</b>：统计达成模式后，手牌是否同时有 4p，且「五」为 5p 或 0p（赤五）</li>
</ul>
"""
