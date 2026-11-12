// Stand-in photographs (Wikimedia Commons, credited in the footer) for the demo menu.
// Replace with the restaurant's own photographs; items without one get a typographic pin.

export interface Media {
  src: string;
  srcSet: string;
  width: number;
  height: number;
  alt: string;
}

const img = (id: string, w: number, h: number, alt: string, big = "960"): Media => ({
  src: `/assets/img/photo-${id}-${big}.jpg`,
  srcSet: `/assets/img/photo-${id}-500.jpg 500w, /assets/img/photo-${id}-${big}.jpg ${big}w`,
  width: w,
  height: h,
  alt,
});

export const MENU_MEDIA: Record<string, Media> = {
  item_pizza_margherita: img("f41882039", 960, 720, "Neapolitan margherita with charred crust, mozzarella and basil on a wooden board"),
  item_pizza_pepperoni: img("f148942734", 960, 540, "Whole pepperoni pizza in an open takeaway box"),
  item_garlic_bread: {
    src: "/assets/img/photo-f155898740-500.jpg",
    srcSet: "/assets/img/photo-f155898740-500.jpg 500w",
    width: 500,
    height: 667,
    alt: "Toasted garlic bread on a white plate",
  },
  item_tiramisu: img("f11769909", 960, 720, "A square of tiramisu dusted with cocoa on a white plate"),
};

export const OVEN: Media = img("f126205963", 960, 640, "Wood fire burning inside a brick pizza oven");

export const PHOTO_CREDITS: { by: string; licence: string; page: string }[] = [
  { by: "Lombroso", licence: "CC BY-SA 4.0", page: "https://commons.wikimedia.org/wiki/File:Pizza_Margherita_().JPG" },
  { by: "YoutubeJarred33 Widower", licence: "CC BY 3.0", page: "https://commons.wikimedia.org/wiki/File:Nice_pepperoni_pizza.jpg" },
  {
    by: "Infrogmation of New Orleans",
    licence: "CC BY-SA 2.0",
    page: "https://commons.wikimedia.org/wiki/File:Garlic_bread_-_on_plate,_ready_to_eat.jpg",
  },
  { by: "Raffaele Diomede", licence: "CC BY 2.0", page: "https://commons.wikimedia.org/wiki/File:Tiramisu_-_Raffaele_Diomede.jpg" },
  { by: "Basheer Tome", licence: "CC BY 2.0", page: "https://commons.wikimedia.org/wiki/File:Wood_fire_brick_oven_for_baking_pizza.jpg" },
];
