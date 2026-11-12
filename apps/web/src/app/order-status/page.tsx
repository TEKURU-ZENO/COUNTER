import { Empty } from "@/components/States";

export default function OrderStatusIndex() {
  return (
    <div className="max-w-xl mx-auto px-4 py-20">
      <Empty
        title="Open your order from its link"
        message="Your ticket is linked from PayPal after you pay, and from the text we send. Each link shows one order."
      />
    </div>
  );
}
