import { Link } from "react-router";
import { Logo } from "../components/Shell";

export default function NotFound() {
  return (
    <div className="grid h-full place-items-center p-6 text-center">
      <div>
        <Logo />
        <h1 className="mt-6 text-xl font-semibold">Nothing here</h1>
        <p className="mt-1 text-sm text-muted">That page doesn't exist.</p>
        <Link to="/" className="mt-4 inline-block text-sm text-brand underline">Back to Today</Link>
      </div>
    </div>
  );
}
