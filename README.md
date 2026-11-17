# Yukisato Nakata

**機械学習を学んでいる学生です。生体信号（睡眠）データの機械学習と、ML研究を楽にするツールづくりをしています。**

*ML student working on sleep / biosignal modeling, and building small tools that make ML research less painful.*

## やっていること

- **生体信号 × 機械学習** — 心拍や脳波などの時系列データから睡眠段階を判定するモデルの開発と、その学習・評価パイプラインの整備（Python / C / Swift）。研究に関わるコードは非公開です。
- **ML研究のためのツール** — 実験の再現性や論文探索など、自分が研究で困ったことを小さなOSSにしています。
- **LLMを使う開発の仕組みづくり** — AIに書かせたコードを自分で理解し続けるための仕組みや、ローカルLLMでの文書処理ツール。

## 作ったもの

| リポジトリ | 内容 | 技術 |
|---|---|---|
| [**seedlint**](https://github.com/nktykst/seedlint) | MLコードの再現性バグを検出するリンター。シード未設定の乱数、`random_state` の付け忘れ、cuDNN の非決定フラグなどを AST の静的解析で見つける。13ルール、テスト50件、CI付き | Python（標準ライブラリのみ） |
| [**arxseek**](https://github.com/nktykst/arxseek) | オフラインで使える arXiv 論文検索CLI。API クライアント、トークナイザ、転置インデックス、BM25 のランキングまで自作 | Python、SQLite |
| [**gradient-descent-arena**](https://github.com/nktykst/gradient-descent-arena) | SGD / Momentum / Adam など6つの最適化アルゴリズムが損失地形の上で競走する可視化。[デモ](https://nktykst.github.io/gradient-descent-arena/) | JavaScript、Canvas（単一HTML） |
| [**comprehension-kit**](https://github.com/nktykst/comprehension-kit) | AIに書かせたコードの「理解負債」を、フックと間隔反復で機械的に返済させる Claude Code 用キット | Python、シェル |
| [**sitedigest**](https://github.com/nktykst/sitedigest) | 保存したサイトや文書（PDF / Office / HTML）をローカルLLMで分割要約するツール。中断しても続きから再開できる | Python、SQLite |

## 技術

- **言語** — Python（メイン）、TypeScript、Swift、C、Rust（学習中）
- **ML** — PyTorch、NumPy、pandas、scikit-learn
- **環境** — uv、Nix、Git / GitHub Actions、Neovim

## 連絡先

nktykst@gmail.com

<br/>

<p align="center">
  <img width="78%" src="img/metrics.svg" alt="GitHub の活動状況（非公開リポジトリを含む）"/>
</p>
