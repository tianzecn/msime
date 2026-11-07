use super::super::ngram::tests::pair_table;
use super::*;
use crate::ime::personal_rerank::allocations::measure;

#[path = "decode_row_index_legacy_tests.rs"]
mod legacy;

fn assert_storage(actual: &Vec<SentencePath>, expected: &Vec<SentencePath>) {
    super::capacity_tests::assert_paths(actual, expected);
    assert_eq!(actual.capacity(), expected.capacity());
    for (actual, expected) in actual.iter().zip(expected) {
        assert_eq!(actual.sentence.capacity(), expected.sentence.capacity());
        assert_eq!(actual.key.capacity(), expected.key.capacity());
        assert_eq!(actual.words.capacity(), expected.words.capacity());
        for (actual, expected) in actual.words.iter().zip(&expected.words) {
            assert_eq!(actual.capacity(), expected.capacity());
        }
    }
}

#[test]
fn small_bigram_row_index_skips_one_allocation() {
    let directory = tempfile::tempdir().unwrap();
    let table = Arc::new(pair_table(
        directory.path(),
        "pairs.bin",
        &[(SENTENCE_START, "甲", 1.0)],
    ));
    for columns in [1, 2, 8, 32] {
        let graph = super::capacity_tests::graph(columns, 1, 0, false);
        let options = LatticeOptions {
            bigram: Some(Arc::clone(&table)),
            ..LatticeOptions::default()
        };
        let (expected, before) = measure(|| legacy::decode_graph(&graph, &options, None));
        let (actual, after) = measure(|| decode_graph(&graph, &options, None));
        assert_storage(&actual, &expected);
        assert_eq!(after.remaining_bytes, before.remaining_bytes);
        assert_eq!(after.allocations + 1, before.allocations, "列数={columns}");
        assert!(after.peak_bytes < before.peak_bytes);
    }
    let graph = super::capacity_tests::graph(8, 4, 0, false);
    let options = LatticeOptions::default();
    let (expected, before) = measure(|| legacy::decode_graph(&graph, &options, None));
    let (actual, after) = measure(|| decode_graph(&graph, &options, None));
    assert_storage(&actual, &expected);
    assert_eq!(after.allocations, before.allocations);
    assert_eq!(after.remaining_bytes, before.remaining_bytes);
}

#[test]
fn small_bigram_row_index_preserves_wide_fallback_and_scores() {
    let directory = tempfile::tempdir().unwrap();
    let table = Arc::new(pair_table(
        directory.path(),
        "pairs.bin",
        &[
            (SENTENCE_START, "甲", 1.0),
            ("甲", "乙", -2.0),
            ("乙", "丙", 0.5),
        ],
    ));
    for columns in [0, 1, 4, 8, 9, 16, 32] {
        for width in [0, 1, 4, 8, 9, 16, 32, 65] {
            let mut graph = super::capacity_tests::graph(columns, width, 0, false);
            for (index, edge) in graph
                .iter_mut()
                .flat_map(|edges| edges.iter_mut())
                .enumerate()
            {
                edge.word = format!("词{index}").into();
                if index % 3 == 0 {
                    edge.log_prob = f64::NAN;
                }
            }
            let extra = super::capacity_tests::graph(columns, width, 1, true);
            for (beam, nbest) in [(0, 0), (1, 5), (4, 0), (8, 12), (32, 5), (65, 65)] {
                for with_extra in [false, true] {
                    let old_memo = RefCell::new(BigramMemo::default());
                    let new_memo = RefCell::new(BigramMemo::default());
                    let options = LatticeOptions {
                        beam,
                        nbest,
                        bigram: Some(Arc::clone(&table)),
                        bigram_memo: Some(&new_memo),
                        ..LatticeOptions::default()
                    };
                    let old_options = LatticeOptions {
                        bigram_memo: Some(&old_memo),
                        ..options.clone()
                    };
                    let extra = with_extra.then_some(&extra);
                    let expected = legacy::decode_graph(&graph, &old_options, extra);
                    let actual = decode_graph(&graph, &options, extra);
                    assert_storage(&actual, &expected);
                    assert_eq!(new_memo.borrow().scores, old_memo.borrow().scores);
                }
            }
        }
    }
}

#[test]
#[ignore = "同机 release 配对计时，普通 CI 不依赖共享机器速度"]
fn small_bigram_row_index_release_comparison() {
    use std::hint::black_box;
    use std::time::{Duration, Instant};

    let directory = tempfile::tempdir().unwrap();
    let table = Arc::new(pair_table(
        directory.path(),
        "pairs.bin",
        &[(SENTENCE_START, "甲", 1.0), ("甲", "乙", -2.0)],
    ));
    let shapes = [
        (0, 0, 32, 5),
        (1, 1, 32, 5),
        (4, 0, 32, 5),
        (4, 4, 32, 5),
        (4, 8, 32, 5),
        (12, 32, 32, 5),
        (32, 32, 32, 5),
        (4, 9, 32, 5),
        (12, 32, 32, 5),
        (4, 65, 65, 65),
        (4, 32, 4, 65),
        (12, 32, 32, 5),
    ];
    let mut regressions = Vec::new();
    let mut target_ratios = Vec::new();
    for &(columns, width, beam, nbest) in &shapes {
        let graph = super::capacity_tests::graph(columns, width, 0, false);
        let extra_graph = super::capacity_tests::graph(columns, width, 1, true);
        for mode in 0..3 {
            for with_extra in [false, true] {
                let extra = with_extra.then_some(&extra_graph);
                let repeats_for = |old: bool, repeats: usize| {
                    let start = Instant::now();
                    for _ in 0..repeats {
                        let memo = RefCell::new(BigramMemo::default());
                        let options = LatticeOptions {
                            beam,
                            nbest,
                            bigram: (mode > 0).then(|| Arc::clone(&table)),
                            bigram_memo: (mode == 2).then_some(&memo),
                            ..LatticeOptions::default()
                        };
                        let rows = if old {
                            legacy::decode_graph(
                                black_box(&graph),
                                black_box(&options),
                                black_box(extra),
                            )
                        } else {
                            decode_graph(black_box(&graph), black_box(&options), black_box(extra))
                        };
                        black_box(rows);
                    }
                    start.elapsed()
                };
                let mut repeats = 1;
                loop {
                    if repeats_for(true, repeats) >= Duration::from_millis(20) {
                        break;
                    }
                    repeats *= 2;
                    assert!(repeats < 1_000_000);
                }
                let mut ratios = Vec::new();
                for pair in 0..21 {
                    let elapsed = if pair % 2 == 0 {
                        [repeats_for(true, repeats), repeats_for(false, repeats)]
                    } else {
                        [repeats_for(false, repeats), repeats_for(true, repeats)]
                    };
                    let ratio = if pair % 2 == 0 {
                        elapsed[1].as_secs_f64() / elapsed[0].as_secs_f64()
                    } else {
                        elapsed[0].as_secs_f64() / elapsed[1].as_secs_f64()
                    };
                    ratios.push(ratio);
                }
                ratios.sort_by(f64::total_cmp);
                let median = ratios[10];
                eprintln!(
                    "small-row-index mode={mode} columns={columns} width={width} beam={beam} nbest={nbest} extra={with_extra} repeats={repeats} median={median:.6} p10={:.6} p90={:.6}",
                    ratios[2],
                    ratios[18]
                );
                if median >= 1.05 {
                    regressions.push((columns, width, beam, nbest, with_extra, median));
                }
                if mode > 0 && columns > 1 && width > 0 && !with_extra {
                    target_ratios.push(median);
                }
            }
        }
    }
    let target_geomean = (target_ratios.iter().map(|ratio| ratio.ln()).sum::<f64>()
        / target_ratios.len() as f64)
        .exp();
    eprintln!("small-row-index target geometric mean={target_geomean:.6}");
    assert!(target_geomean < 0.98, "小索引目标组没有达到收益门槛");
    assert!(regressions.is_empty(), "回退超过预算：{regressions:?}");
}
