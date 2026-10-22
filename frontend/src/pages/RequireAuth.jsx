import { Navigate, useLocation } from "react-router";
import { Spinner } from "../components/ui";
import { useMe } from "../lib/auth";

export default function RequireAuth({ children }) {
  const { data, error, isLoading } = useMe();
  const location = useLocation();
  if (isLoading) return <div className="grid h-full place-items-center text-muted"><Spinner /></div>;
  if (error?.status === 410) return <Navigate to="/login?ended=1" replace />;
  if (error || !data || data.kind !== "user") return <Navigate to={`/login?next=${encodeURIComponent(location.pathname)}`} replace />;
  if (!data.workspace_id) return <Navigate to="/login?no_workspace=1" replace />;
  return children;
}
