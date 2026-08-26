import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/** Catches a render-time crash anywhere below it so ONE broken widget (e.g.
 * a completeness badge choking on an old-schema sidecar -- see api.ts's
 * fileCompleteness()) can't blank the entire page with no way to recover
 * short of a hard reload. React error boundaries must be class components;
 * there's no hooks equivalent. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Render crashed:", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="page">
          <p className="error">Something went wrong rendering this page: {this.state.error.message}</p>
          <button className="secondary" onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
