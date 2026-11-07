use super::*;
use crate::ime::personal_rerank::allocations::{count, measure};

fn baseline_build_graph(
    syllables: &[String],
    lookup: &mut LatticeLookup<'_>,
    options: &LatticeOptions<'_>,
) -> Graph {
    let n = syllables.len();
    let max_len = options.max_phrase_syllables.max(1);
    let mut graph: Graph = (0..n).map(|_| Vec::new()).collect();
    let span_capacity = (0..n)
        .map(|start| n.min(start.saturating_add(max_len)) - start)
        .sum();
    let mut span_cache: HashMap<&[String], Vec<LatticeLexeme>> =
        HashMap::with_capacity(span_capacity);
    for (start, edges) in graph.iter_mut().enumerate() {
        for end in start + 1..=n.min(start + max_len) {
            let span = &syllables[start..end];
            let rows = span_cache.entry(span).or_insert_with(|| lookup(span));
            edges.reserve(rows.len().min(options.span_limit));
            let mut span_key = None;
            for row in rows.iter().take(options.span_limit) {
                if row.value.is_empty() {
                    continue;
                }
                edges.push(Edge {
                    end,
                    word: row.value.clone(),
                    key: if row.key.is_empty() {
                        span_key
                            .get_or_insert_with(|| span.join("'"))
                            .clone()
                            .into()
                    } else {
                        row.key.clone()
                    },
                    log_prob: edge_log_prob(row.weight, end - start, options),
                    typo: false,
                });
            }
        }
    }
    graph
}

fn fixture_rows(span: &[String], profile: usize, call: usize) -> Vec<LatticeLexeme> {
    if profile == 3 {
        return Vec::new();
    }
    let key = span.join("'");
    [-7, 0, 1000, i64::MAX]
        .into_iter()
        .enumerate()
        .map(|(index, weight)| LatticeLexeme {
            key: if profile == 2 || (profile == 1 && index % 2 == 0) {
                String::new().into()
            } else {
                key.clone().into()
            },
            value: if (profile == 1 && index == 1) || (profile == 2 && index == 2) {
                String::new().into()
            } else {
                format!("合成{call}号{index}😀").into()
            },
            weight,
        })
        .collect()
}

fn assert_same_graph(old: &Graph, new: &Graph) {
    assert_eq!(new.len(), old.len());
    assert_eq!(new.capacity(), old.capacity());
    for (before, after) in old.iter().zip(new) {
        assert_eq!(after.len(), before.len());
        assert_eq!(after.capacity(), before.capacity());
        for (a, b) in before.iter().zip(after) {
            assert_eq!(
                (b.end, &b.word, &b.key, b.log_prob.to_bits(), b.typo),
                (a.end, &a.word, &a.key, a.log_prob.to_bits(), a.typo)
            );
        }
    }
}

#[test]
fn graph_build_transfers_unique_rows_and_preserves_repeated_spans() {
    let mut cases = 0;
    for input in [
        None,
        Some(""),
        Some("a"),
        Some("a'ba'ca"),
        Some("a'a'a'a"),
        Some("a'ba'a'ba'a"),
        Some("a''a"),
        Some("😀'a'😀"),
    ] {
        let keys: Vec<String> = input
            .map(|text| text.split('\'').map(str::to_owned).collect())
            .unwrap_or_default();
        for max_phrase_syllables in [0, 1, 3, 9] {
            for span_limit in [0, 1, 2, 5, usize::MAX] {
                for profile in 0..4 {
                    let options = LatticeOptions {
                        max_phrase_syllables,
                        span_limit,
                        ..LatticeOptions::default()
                    };
                    let run = |baseline| {
                        let mut calls = Vec::new();
                        let mut lookup = |span: &[String]| {
                            let rows = fixture_rows(span, profile, calls.len());
                            calls.push(span.to_vec());
                            rows
                        };
                        let graph = if baseline {
                            baseline_build_graph(&keys, &mut lookup, &options)
                        } else {
                            build_graph(&keys, &mut lookup, &options)
                        };
                        (graph, calls)
                    };
                    let ((old, old_calls), before) = measure(|| run(true));
                    let ((new, new_calls), after) = measure(|| run(false));
                    assert_same_graph(&old, &new);
                    assert_eq!(new_calls, old_calls);
                    assert_eq!(
                        new_calls.iter().collect::<HashSet<_>>().len(),
                        new_calls.len()
                    );
                    // 边的文本字段是 `SharedString`，冻结实现的克隆也只加引用计数，不再有逐行可省的申请；只要求不比它多。
                    assert!(after.allocations <= before.allocations,
                        "输入={input:?}/最大词长={max_phrase_syllables}/限额={span_limit}/行={profile}");
                    assert_eq!(before.minimum_bytes, 0);
                    assert_eq!(after.minimum_bytes, 0);
                    let old_paths = decode_graph(&old, &options, None);
                    let new_paths = decode_graph(&new, &options, None);
                    assert_eq!(new_paths, old_paths);
                    if keys.len() >= 3 && !old_paths.is_empty() {
                        let typo = [TypoEdge {
                            start: 1,
                            end: 3,
                            key: "合成读音".into(),
                            value: "合成纠错".into(),
                            weight: 900_000,
                            penalty: 0.5,
                        }];
                        assert_eq!(
                            decode_typo_on_graph(&new, &options, &new_paths[0], &typo),
                            decode_typo_on_graph(&old, &options, &old_paths[0], &typo)
                        );
                    }
                    if input == Some("a'ba'ca") && max_phrase_syllables == 3 && span_limit == 5 {
                        eprintln!("graph ownership profile={profile}: allocations {} -> {}, peak {} -> {}, returned {} -> {}",
                            before.allocations, after.allocations, before.peak_bytes, after.peak_bytes,
                            before.remaining_bytes, after.remaining_bytes);
                    }
                    cases += 1;
                }
            }
        }
    }
    assert_eq!(cases, 640);
}

#[test]
fn first_graph_occurrence_owns_the_original_row_storage() {
    let keys: Vec<String> = ["a", "ba", "a"].into_iter().map(str::to_owned).collect();
    let options = LatticeOptions {
        max_phrase_syllables: 1,
        ..LatticeOptions::default()
    };
    let mut pointers = Vec::with_capacity(2);
    let mut lookup = |span: &[String]| {
        let mut word = String::with_capacity(128);
        word.push_str("合成词面😀");
        let mut key = String::with_capacity(96);
        key.push_str(&span[0]);
        pointers.push((word.as_ptr(), word.capacity(), key.as_ptr(), key.capacity()));
        vec![LatticeLexeme {
            value: word.into(),
            key: key.into(),
            weight: 123,
        }]
    };
    let (graph, allocations) = count(|| build_graph(&keys, &mut lookup, &options));
    assert_eq!(pointers.len(), 2);
    for (column, expected) in graph.iter().take(2).zip(pointers) {
        let edge = &column[0];
        assert_eq!(
            (
                edge.word.as_ptr(),
                edge.word.capacity(),
                edge.key.as_ptr(),
                edge.key.capacity()
            ),
            expected
        );
    }
    assert_eq!(graph[0][0].word, graph[2][0].word);
    assert_eq!(graph[0][0].key, graph[2][0].key);
    // 重复跨度的边与首次出现共用文本存储，克隆只加引用计数。
    assert!(graph[0][0].word.shares_storage_with(&graph[2][0].word));
    assert!(graph[0][0].key.shares_storage_with(&graph[2][0].key));
    assert_eq!(
        (graph[0][0].end, graph[1][0].end, graph[2][0].end),
        (1, 2, 3)
    );
    assert_eq!(
        allocations, 15,
        "图/哈希表各一次、两次查询各五次（两个字段的字符串和 Arc、行容器）、三列各一次，重复跨度共用存储不再申请"
    );
}
