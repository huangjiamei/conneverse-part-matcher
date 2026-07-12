import json

d = json.load(open('response.json'))
print('=== 顶层结构 ===')
print('label:', d.get('label'))
print('label_source:', d.get('label_source'))
print()

meta = d.get('dataset_meta', {})
print('=== 检索过程 ===')
print('level_used (最终):', meta.get('level_used'))
print('category_id:', meta.get('category_id_used'))
print('supports_compat:', meta.get('supports_compat_used'))
print('match_rank:', meta.get('match_rank'))
print()

print('三档尝试结果:')
for t in meta.get('tried_levels', []):
    level = t.get('level', '')
    query = repr(t.get('query'))
    count = t.get('resultCount')
    print(f'  {level:8} query={query:50}  resultCount={count}')

print()
print('=== 候选结果 ===')
cands = d.get('candidate_info_list', [])
print(f'总候选数: {len(cands)}')
for i, c in enumerate(cands, 1):
    label = c.get('candidate_label')
    src = c.get('candidate_label_source')
    title = (c.get('title') or '')[:70]
    price = (c.get('price') or {}).get('value')
    print(f'  {i:2}. label={label} ({src})')
    print(f'      ${price}  {title}')

print()
print('=== 阶段计数 ===')
print(meta.get('post_mpn_stage_counts'))