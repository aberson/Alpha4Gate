import { useBuildOrders } from "../hooks/useBuildOrders";

export function BuildOrderEditor() {
  const { orders, loading, deleteOrder } = useBuildOrders();

  if (loading) return <p>Loading build orders...</p>;

  return (
    <div className="build-orders">
      <h2>Build Orders</h2>
      {orders.length === 0 ? (
        <p>No build orders defined.</p>
      ) : (
        <ul>
          {orders.map((order) => (
            <li key={order.id}>
              <strong>{order.name}</strong> ({order.steps.length} steps, source: {order.source})
              <button onClick={() => deleteOrder(order.id)} style={{ marginLeft: 8 }}>
                Delete
              </button>
              <ol>
                {order.steps.map((step, i) => (
                  <li key={i}>
                    @{step.supply} supply: {step.action} {step.target}
                  </li>
                ))}
              </ol>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
