import Segmented from './Segmented';

export type GridCols = 1 | 2 | 3;

interface Props {
  value: GridCols;
  onChange: (v: GridCols) => void;
}

export default function GridSelector({ value, onChange }: Props) {
  return (
    <Segmented<string>
      options={[
        { value: '1', label: '1×1' },
        { value: '2', label: '2×2' },
        { value: '3', label: '3×3' },
      ]}
      value={String(value)}
      onChange={(v) => onChange(Number(v) as GridCols)}
    />
  );
}
