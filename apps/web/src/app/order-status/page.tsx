import { Empty } from "@/components/States";

export default function OrderStatusIndex() {
  return (
    <div className="max-w-xl mx-auto px-4 py-16">
      <Empty
        title="Open your order from its link"
        message="Your order status page is linked from the PayPal checkout and from the text we send. Each link only shows one order."
      />
    </div>
  );
}
