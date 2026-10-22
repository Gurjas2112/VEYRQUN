import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api("/auth/me"), retry: false, staleTime: 30_000 });
}

/** Permissions come from the server (/auth/me) and only shape the UI. The server enforces them on every call. */
export function useCan() {
  const { data } = useMe();
  const perms = new Set(data?.permissions ?? []);
  return (perm) => perms.has(perm);
}

export function Can({ perm, children, fallback = null }) {
  const can = useCan();
  return can(perm) ? children : fallback;
}
