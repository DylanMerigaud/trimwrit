// vendored from approvals-ui (https://github.com/DylanMerigaud/approvals-ui), forked for trimwrit
import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export const cn = (...inputs: ClassValue[]) => {
  return twMerge(clsx(inputs));
};
