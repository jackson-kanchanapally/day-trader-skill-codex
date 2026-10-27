"""Portable text fan chart for saved Indian Time Machine scenarios."""


def render(doc, height=17):
    mc, plan = doc.get('mc'), doc.get('plan') or {}
    if doc.get('status') != 'AVAILABLE' or not mc or not mc.get('bands'):
        return 'No available sampled scenario bands in this saved report.'
    bands = mc['bands']
    if not all(isinstance(row, list) and len(row) == 5 for row in bands):
        raise ValueError('Invalid saved scenario bands')
    marks = {key: plan.get(key) for key in ('entry', 'stop', 'target') if isinstance(plan.get(key), (int, float))}
    low = min([row[0] for row in bands] + list(marks.values()))
    high = max([row[-1] for row in bands] + list(marks.values()))
    pad = (high-low)*.04 or .01
    high, low = high+pad, low-pad
    step = (high-low)/(height-1)
    place = lambda value: max(0, min(height-1, round((high-value)/step)))
    grid = [[' ']*len(bands) for _ in range(height)]
    for col, row in enumerate(bands):
        p5, p25, p50, p75, p95 = map(place, row)
        for index in range(p95, p5+1):
            grid[index][col] = '.'
        for index in range(p75, p25+1):
            grid[index][col] = ':'
        grid[p50][col] = '*'
    for label in ('target', 'stop'):
        if label in marks:
            grid[place(marks[label])] = ['-' if char == ' ' else char for char in grid[place(marks[label])]]
    labels = {place(price): label for label, price in marks.items()}
    lines = ['%s · %s · %s sampled paths · %s to %s IST' % (
        plan.get('symbol', 'Symbol'), plan.get('date', 'session'), mc.get('band_paths', '?'),
        plan.get('entry_time', '?'), plan.get('flat_by', '?'))]
    for index in range(height):
        label = labels.get(index, '')
        lines.append('%9.2f |%s%s' % (high-index*step, ''.join(grid[index]), '  '+label if label else ''))
    lines.append('          +'+('-'*len(bands)))
    lines.append('* median   : middle half   . 90% of sampled paths   - stop/target')
    return '\n'.join(lines)
