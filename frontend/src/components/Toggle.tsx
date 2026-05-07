import { useState } from 'react';

interface Props {
  defaultOn?: boolean;
  onChange?: (on: boolean) => void;
}

export default function Toggle({ defaultOn = false, onChange }: Props) {
  const [on, setOn] = useState(defaultOn);
  return (
    <button
      type="button"
      className={`toggle ${on ? 'on' : ''}`}
      aria-pressed={on}
      onClick={() => {
        const next = !on;
        setOn(next);
        onChange?.(next);
      }}
    />
  );
}
