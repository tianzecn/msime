use crate::types::{CandidateSource, SchemeType, SharedString, WordItem};

/// 普通页只保存变化的字段；展开页继续持有原完整候选和容量。
#[derive(Clone, Debug, PartialEq)]
pub(super) enum PathPage {
    Empty,
    Full(Vec<WordItem>),
    Compact(Box<CompactPage>),
}

/// 压缩页的文本字段与产生它的候选行共用 `SharedString` 存储：存页和取页都只加引用计数，不复制字符串。整页的匹配读音只存一份，每个输出行共用它。
#[derive(Clone, Debug, PartialEq)]
pub(super) struct CompactPage {
    pinyin: SharedString,
    rows: Vec<CompactRow>,
}

#[derive(Clone, Debug, PartialEq)]
pub(super) struct CompactRow {
    canonical_pinyin: SharedString,
    word: SharedString,
    weight: i64,
}

impl From<Vec<WordItem>> for PathPage {
    fn from(rows: Vec<WordItem>) -> Self {
        Self::Full(rows)
    }
}

impl PathPage {
    pub(super) fn from_dictionary(rows: &[WordItem]) -> Self {
        let Some(first) = rows.first() else {
            return Self::Empty;
        };
        // 单行页压缩后省不下行结构，反而多一个 `Box`。
        if first.pinyin.is_empty()
            || rows.len() <= 1
            || !rows.iter().all(|row| ordinary_row(row, &first.pinyin))
        {
            return Self::Full(rows.to_vec());
        }
        let mut compact = Vec::with_capacity(rows.len());
        compact.extend(rows.iter().map(|row| CompactRow {
            canonical_pinyin: row.canonical_pinyin.clone(),
            word: row.word.clone(),
            weight: row.weight,
        }));
        Self::Compact(Box::new(CompactPage {
            pinyin: first.pinyin.clone(),
            rows: compact,
        }))
    }

    pub(super) fn len(&self) -> usize {
        match self {
            Self::Empty => 0,
            Self::Full(rows) => rows.len(),
            Self::Compact(page) => page.rows.len(),
        }
    }

    pub(super) fn copy_rows(&self) -> Vec<WordItem> {
        match self {
            Self::Empty => Vec::new(),
            Self::Full(rows) => rows.clone(),
            Self::Compact(page) => {
                let mut result = Vec::with_capacity(page.rows.len());
                result.extend(page.rows.iter().map(|row| copy_row(row, &page.pinyin)));
                result
            }
        }
    }

    pub(super) fn append_to(&self, result: &mut Vec<WordItem>) {
        result.reserve(self.len());
        match self {
            Self::Empty => {}
            Self::Full(rows) => result.extend(rows.iter().cloned()),
            Self::Compact(page) => {
                result.extend(page.rows.iter().map(|row| copy_row(row, &page.pinyin)));
            }
        }
    }

    pub(super) fn cloned_rows(&self) -> ClonedRows<'_> {
        match self {
            Self::Empty => ClonedRows::Full([].iter()),
            Self::Full(rows) => ClonedRows::Full(rows.iter()),
            Self::Compact(page) => ClonedRows::Compact {
                pinyin: &page.pinyin,
                rows: page.rows.iter(),
            },
        }
    }
}

fn ordinary_row(row: &WordItem, reading: &str) -> bool {
    // 穷尽字段，使新增元数据必须明确选择保存或拒绝压缩。
    let WordItem {
        pinyin,
        canonical_pinyin: _,
        word: _,
        weight: _,
        source,
        scheme,
        fixed_position,
        fuzzy,
        corrected_from,
        sentence_association,
        sentence_words,
        touch_supported,
        confident_correction,
    } = row;
    pinyin == reading
        && *source == CandidateSource::Database
        && *scheme == SchemeType::Quanpin
        && *fixed_position == 0
        && !fuzzy
        && corrected_from.is_empty()
        && !sentence_association
        && sentence_words.is_empty()
        && !touch_supported
        && !confident_correction
}

/// 还原 `WordItem::new` 给词库行的默认元数据；文本字段只加引用计数。
fn copy_row(row: &CompactRow, pinyin: &SharedString) -> WordItem {
    WordItem {
        pinyin: pinyin.clone(),
        canonical_pinyin: row.canonical_pinyin.clone(),
        word: row.word.clone(),
        weight: row.weight,
        source: CandidateSource::Database,
        ..WordItem::default()
    }
}

pub(super) enum ClonedRows<'a> {
    Full(std::slice::Iter<'a, WordItem>),
    Compact {
        pinyin: &'a SharedString,
        rows: std::slice::Iter<'a, CompactRow>,
    },
}

impl Iterator for ClonedRows<'_> {
    type Item = WordItem;

    fn next(&mut self) -> Option<Self::Item> {
        match self {
            Self::Full(rows) => rows.next().cloned(),
            Self::Compact { pinyin, rows } => rows.next().map(|row| copy_row(row, pinyin)),
        }
    }

    fn size_hint(&self) -> (usize, Option<usize>) {
        let len = match self {
            Self::Full(rows) => rows.len(),
            Self::Compact { rows, .. } => rows.len(),
        };
        (len, Some(len))
    }
}

impl ExactSizeIterator for ClonedRows<'_> {}
