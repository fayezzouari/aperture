import { useEffect, useState } from "react";

export function useReducedMotion() {
  const [reduce, setReduce] = useState(() =>
    typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)").matches : false,
  );
  useEffect(() => {
    const mq = matchMedia("(prefers-reduced-motion: reduce)");
    const on = () => setReduce(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return reduce;
}
