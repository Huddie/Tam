/** Cross-site switcher, identical (by convention, not by import -- these are
 * two separate Cloudflare Workers/deployments) to tam-data-explorer's own
 * NavBar. Cross-site links are plain <a> (a different origin/Worker
 * entirely, not a client-side route); same-site links would use
 * react-router's <Link> instead, but there are none here today. */
export function NavBar({ current }: { current: "discovery" | "data" }) {
  return (
    <div className="topnav">
      <a className={current === "discovery" ? "active" : ""} href="https://discovery.tamquant.com/">
        Discovery
      </a>
      <a className={current === "data" ? "active" : ""} href="https://data.tamquant.com/">
        Data Explorer
      </a>
    </div>
  );
}
