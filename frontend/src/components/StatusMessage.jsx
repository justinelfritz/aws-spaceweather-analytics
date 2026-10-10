export default function StatusMessage({ kind, children }) {
  return (
    <div className={`status status-${kind}`} role={kind === "error" ? "alert" : "status"}>
      {children}
    </div>
  );
}
